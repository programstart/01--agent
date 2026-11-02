from __future__ import annotations

import hashlib
import re
import sqlite3
from datetime import UTC, datetime, timedelta
from typing import Any

from .models import (
    AlternativeSlot,
    AuditCheckInput,
    AuditCheckResult,
    AuditCheckStatus,
    AuditRuleRecord,
    AuditStatus,
    AuditSummary,
    ConflictRecord,
    ConfirmationReason,
    DataCompleteness,
    DraftSaveStatus,
    DraftStatus,
    DraftView,
    MaterialReadInput,
    MaterialReadResult,
    MaterialReadStatus,
    MaterialRecord,
    MaterialView,
    RuleEvaluation,
    RuleKind,
    RuleOperator,
    RuleOutcome,
    RuleSeverity,
    SaveDraftResult,
    SaveDraftContext,
    SaveDraftInput,
    ScheduleQueryContext,
    ScheduleQueryInput,
    ScheduleQueryResult,
    ScheduleQueryStatus,
    SemanticOutcome,
    SourceStatus,
    ToolError,
)
from .repositories import (
    DraftRepository,
    DraftSlotConflictError,
    MaterialRepository,
    OccupancyRow,
    RepositoryDataError,
    RuleRepository,
    ScheduleRepository,
)
from .semantic_rules import SemanticRuleEvaluator
from .time_utils import (
    generate_grid_candidates,
    normalize_datetime,
    utc_key,
    validate_search_window,
)


MISSING = object()
ASSET_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


def is_missing(value: Any) -> bool:
    return value is MISSING or value is None or value == "" or value == [] or value == {}


def intervals_overlap(
    left_start: datetime,
    left_minutes: int,
    right_start: datetime,
    right_minutes: int,
) -> bool:
    left_end = left_start + timedelta(minutes=left_minutes)
    right_end = right_start + timedelta(minutes=right_minutes)
    return left_start < right_end and right_start < left_end


class MaterialService:
    def __init__(self, repository: MaterialRepository):
        self.repository = repository

    def read(self, payload: MaterialReadInput) -> MaterialReadResult:
        asset_id = payload.asset_id.strip()
        if not ASSET_ID_PATTERN.fullmatch(asset_id):
            return MaterialReadResult(
                status=MaterialReadStatus.INVALID_ARGUMENT,
                error=ToolError(code="INVALID_ASSET_ID", message="素材 ID 格式不合法"),
                meta={"contract_version": "0.2"},
            )
        try:
            record = self.repository.get(asset_id)
        except RepositoryDataError as exc:
            return MaterialReadResult(
                status=MaterialReadStatus.INCOMPLETE,
                error=ToolError(code="MALFORMED_METADATA", message=str(exc)),
                meta={"contract_version": "0.2"},
            )
        except sqlite3.Error:
            return MaterialReadResult(
                status=MaterialReadStatus.STORAGE_ERROR,
                error=ToolError(
                    code="MATERIAL_STORAGE_UNAVAILABLE",
                    message="素材数据源暂时不可用",
                    retryable=True,
                ),
                meta={"contract_version": "0.2"},
            )

        if record is None:
            return MaterialReadResult(
                status=MaterialReadStatus.NOT_FOUND,
                error=ToolError(code="MATERIAL_NOT_FOUND", message="未找到指定素材"),
                meta={"contract_version": "0.2"},
            )

        missing: list[str] = []
        for field in ("title", "platform", "source_status"):
            if is_missing(getattr(record, field)):
                missing.append(field)
        if not isinstance(record.metadata, dict):
            missing.append("metadata")

        source_status: SourceStatus | None = None
        if record.source_status:
            try:
                source_status = SourceStatus(record.source_status)
            except ValueError:
                missing.append("source_status")

        content = record.content or ""
        view = self._to_view(record, source_status, content)
        completeness = DataCompleteness(complete=not missing, missing_fields=missing)
        if missing:
            return MaterialReadResult(
                status=MaterialReadStatus.INCOMPLETE,
                material=view,
                data_completeness=completeness,
                error=ToolError(
                    code=(
                        "INVALID_SOURCE_STATUS"
                        if "source_status" in missing
                        else "MATERIAL_FIELDS_MISSING"
                    ),
                    message="素材基础字段不完整",
                ),
                meta={"contract_version": "0.2", "content_limit_chars": 1000},
            )

        return MaterialReadResult(
            status=MaterialReadStatus.SUCCESS,
            material=view,
            data_completeness=completeness,
            meta={
                "contract_version": "0.2",
                "query_mode": "primary_key",
                "content_limit_chars": 1000,
            },
        )

    @staticmethod
    def _to_view(
        record: MaterialRecord,
        source_status: SourceStatus | None,
        content: str,
    ) -> MaterialView:
        return MaterialView(
            asset_id=record.asset_id,
            title=record.title,
            platform=record.platform,
            source_status=source_status,
            metadata=record.metadata,
            content_excerpt=content[:1000] or None,
            content_truncated=len(content) > 1000,
            updated_at=record.updated_at,
        )


class RuleEngine:
    def __init__(
        self,
        material_repository: MaterialRepository,
        rule_repository: RuleRepository,
        semantic_evaluator: SemanticRuleEvaluator,
    ):
        self.material_repository = material_repository
        self.rule_repository = rule_repository
        self.semantic_evaluator = semantic_evaluator

    def check(self, payload: AuditCheckInput) -> AuditCheckResult:
        try:
            material = self.material_repository.get(payload.asset_id)
            rules = self.rule_repository.list_enabled()
        except RepositoryDataError as exc:
            return AuditCheckResult(
                status=AuditCheckStatus.ERROR,
                error=ToolError(code="RULE_CONFIG_INVALID", message="规则配置无效"),
                meta={"contract_version": "0.3", "detail": str(exc)},
            )
        except sqlite3.Error:
            return AuditCheckResult(
                status=AuditCheckStatus.ERROR,
                error=ToolError(
                    code="RULE_SET_UNAVAILABLE",
                    message="规则库暂时不可用",
                    retryable=True,
                ),
                meta={"contract_version": "0.3"},
            )

        if material is None:
            return AuditCheckResult(
                status=AuditCheckStatus.ERROR,
                error=ToolError(code="MATERIAL_NOT_FOUND", message="未找到待审核素材"),
                meta={"contract_version": "0.3"},
            )

        now = datetime.now(UTC)
        applicable = [
            rule
            for rule in rules
            if self._is_applicable(rule, material.platform, now)
        ]
        if not applicable:
            return AuditCheckResult(
                status=AuditCheckStatus.ERROR,
                error=ToolError(
                    code="NO_APPLICABLE_RULES",
                    message="没有可用于当前素材的审核规则",
                ),
                meta={"contract_version": "0.3", "rule_set_version": "phase1-v2"},
            )

        request_context = {
            "requested_at": payload.requested_at,
            "requested_at_utc": payload.requested_at.astimezone(UTC),
            "timezone": payload.timezone,
        }
        structured_rules = [r for r in applicable if r.rule_kind == RuleKind.STRUCTURED]
        semantic_rules = [r for r in applicable if r.rule_kind == RuleKind.SEMANTIC]
        all_semantic_rules = list(semantic_rules)
        evaluations: list[RuleEvaluation] = []
        for rule in structured_rules:
            evaluations.append(self._evaluate_structured(rule, material, request_context))

        global_semantic_error: str | None = None
        invalid_semantic_rules = [
            rule for rule in semantic_rules if is_missing(rule.policy_text)
        ]
        if invalid_semantic_rules:
            global_semantic_error = "语义规则缺少 policy_text"
            evaluations.extend(
                self._rule_evaluations(
                    invalid_semantic_rules,
                    RuleOutcome.ERROR,
                    reason=global_semantic_error,
                    evaluator="rule-configuration",
                    error_code="SEMANTIC_POLICY_MISSING",
                )
            )
            semantic_rules = [
                rule for rule in semantic_rules if rule not in invalid_semantic_rules
            ]
        if semantic_rules:
            if is_missing(material.content):
                evaluations.extend(self._rule_evaluations(
                    semantic_rules,
                    RuleOutcome.MISSING,
                    reason="语义审核需要完整素材内容",
                    missing_fields=["material.content"],
                    evaluator=self.semantic_evaluator.name,
                ))
            else:
                try:
                    batch = self.semantic_evaluator.evaluate(material, semantic_rules)
                except Exception as exc:
                    global_semantic_error = f"语义审核器执行失败: {type(exc).__name__}"
                    evaluations.extend(self._rule_evaluations(
                        semantic_rules,
                        RuleOutcome.ERROR,
                        reason=global_semantic_error,
                        evaluator=self.semantic_evaluator.name,
                        error_code="SEMANTIC_EVALUATOR_FAILED",
                    ))
                else:
                    judgments_by_rule: dict[str, Any] = {}
                    duplicates: set[str] = set()
                    for judgment in batch.judgments:
                        if judgment.rule_id in judgments_by_rule:
                            duplicates.add(judgment.rule_id)
                        judgments_by_rule[judgment.rule_id] = judgment
                    expected_ids = {rule.rule_id for rule in semantic_rules}
                    unknown_ids = set(judgments_by_rule) - expected_ids
                    batch_errors: list[str] = []
                    if duplicates:
                        batch_errors.append(
                            "重复规则: " + ", ".join(sorted(duplicates))
                        )
                    if unknown_ids:
                        batch_errors.append(
                            "未知规则: " + ", ".join(sorted(unknown_ids))
                        )
                    if batch_errors:
                        global_semantic_error = "语义审核器返回了" + "；".join(batch_errors)
                    for rule in semantic_rules:
                        judgment = judgments_by_rule.get(rule.rule_id)
                        if rule.rule_id in duplicates:
                            evaluations.append(
                                self._single_rule_evaluation(
                                    rule,
                                    RuleOutcome.ERROR,
                                    reason="语义审核器对同一规则返回了多个结果",
                                    evaluator=self.semantic_evaluator.name,
                                    error_code="SEMANTIC_RESULT_DUPLICATE",
                                )
                            )
                            continue
                        if unknown_ids:
                            evaluations.append(
                                self._single_rule_evaluation(
                                    rule,
                                    RuleOutcome.ERROR,
                                    reason=(
                                        "语义审核器返回了未知规则，"
                                        "本轮全部语义结果均不可信"
                                    ),
                                    evaluator=self.semantic_evaluator.name,
                                    error_code="SEMANTIC_UNKNOWN_RULE",
                                )
                            )
                            continue
                        if judgment is None:
                            evaluations.append(
                                self._single_rule_evaluation(
                                    rule,
                                    RuleOutcome.ERROR,
                                    reason="语义审核器未返回该规则结果",
                                    evaluator=self.semantic_evaluator.name,
                                    error_code="SEMANTIC_RESULT_MISSING",
                                )
                            )
                            continue
                        evidence = [item for item in judgment.evidence_spans if isinstance(item, str)]
                        invalid_evidence = (
                            judgment.outcome == SemanticOutcome.VIOLATED
                            and (
                                not evidence
                                or any(item not in (material.content or "") for item in evidence)
                            )
                        )
                        outcome = {
                            SemanticOutcome.PASSED: RuleOutcome.PASSED,
                            SemanticOutcome.VIOLATED: RuleOutcome.VIOLATED,
                            SemanticOutcome.NOT_APPLICABLE: RuleOutcome.NOT_APPLICABLE,
                            SemanticOutcome.UNCERTAIN: RuleOutcome.UNCERTAIN,
                        }[judgment.outcome]
                        if invalid_evidence:
                            outcome = RuleOutcome.ERROR
                        evaluations.append(
                            self._single_rule_evaluation(
                                rule,
                                outcome,
                                reason=judgment.reason or rule.reason,
                                evidence_spans=evidence,
                                confidence=judgment.confidence,
                                evaluator=self.semantic_evaluator.name,
                                error_code=(
                                    "SEMANTIC_EVIDENCE_INVALID"
                                    if invalid_evidence
                                    else None
                                ),
                            )
                        )

        summary = self._summarize(evaluations, structured_rules, all_semantic_rules)
        if summary.blocking_violation_count:
            status = AuditCheckStatus.BLOCKED
        elif summary.error_rule_count or global_semantic_error:
            status = AuditCheckStatus.ERROR
        elif summary.missing_rule_count:
            status = AuditCheckStatus.NEEDS_INFO
        elif summary.uncertain_rule_count:
            status = AuditCheckStatus.NEEDS_REVIEW
        elif summary.warning_violation_count:
            status = AuditCheckStatus.WARNING
        else:
            status = AuditCheckStatus.PASS

        top_error = None
        if summary.error_rule_count or global_semantic_error:
            top_error = ToolError(
                code="RULE_EVALUATION_ERROR",
                message=global_semantic_error or "部分审核规则执行失败",
            )

        return AuditCheckResult(
            status=status,
            rule_results=evaluations,
            summary=summary,
            error=top_error,
            meta={
                "contract_version": "0.3",
                "rule_set_version": "phase1-v2",
                "evidence_limit_chars": 200,
                "semantic_evaluator": self.semantic_evaluator.name,
            },
        )

    @staticmethod
    def _is_applicable(rule: AuditRuleRecord, platform: str | None, now: datetime) -> bool:
        if rule.effective_from and now < rule.effective_from:
            return False
        if rule.effective_to and now > rule.effective_to:
            return False
        return not rule.applies_to or (platform is not None and platform in rule.applies_to)

    def _evaluate_structured(
        self,
        rule: AuditRuleRecord,
        material: MaterialRecord,
        request_context: dict[str, Any],
    ) -> RuleEvaluation:
        if not rule.conditions:
            return self._single_rule_evaluation(
                rule,
                RuleOutcome.ERROR,
                reason="结构化规则没有检查条件",
                evaluator="structured-rule-engine",
                error_code="RULE_CONDITION_MISSING",
            )

        outcomes: list[RuleOutcome] = []
        evidence: list[str] = []
        missing_fields: list[str] = []
        for condition in rule.conditions:
            outcome, actual = self._evaluate_condition(
                condition,
                material,
                request_context,
            )
            outcomes.append(outcome)
            if outcome == RuleOutcome.MISSING:
                missing_fields.append(condition.field_path)
            if outcome == RuleOutcome.VIOLATED:
                evidence.append(self._condition_evidence(condition, actual))

        if RuleOutcome.ERROR in outcomes:
            outcome = RuleOutcome.ERROR
        elif RuleOutcome.VIOLATED in outcomes:
            outcome = RuleOutcome.VIOLATED
        elif RuleOutcome.MISSING in outcomes:
            outcome = RuleOutcome.MISSING
        else:
            outcome = RuleOutcome.PASSED
        return self._single_rule_evaluation(
            rule,
            outcome,
            reason=rule.reason,
            evidence_spans=[item for item in evidence if item],
            missing_fields=missing_fields,
            evaluator="structured-rule-engine",
            error_code="RULE_EVALUATION_ERROR" if outcome == RuleOutcome.ERROR else None,
        )

    def _evaluate_condition(
        self,
        condition,
        material: MaterialRecord,
        request_context: dict[str, Any],
    ) -> tuple[RuleOutcome, Any]:
        try:
            actual = self._resolve_field(condition.field_path, material, request_context)
        except (KeyError, ValueError):
            return RuleOutcome.ERROR, None

        if condition.operator == RuleOperator.REQUIRED:
            return (RuleOutcome.MISSING if is_missing(actual) else RuleOutcome.PASSED), actual
        if actual is MISSING:
            return RuleOutcome.MISSING, None

        expected = condition.expected_value
        if condition.operator == RuleOperator.EQUALS:
            return (RuleOutcome.PASSED if actual == expected else RuleOutcome.VIOLATED), actual
        if condition.operator == RuleOperator.IN:
            if not isinstance(expected, list):
                return RuleOutcome.ERROR, actual
            return (RuleOutcome.PASSED if actual in expected else RuleOutcome.VIOLATED), actual
        if condition.operator == RuleOperator.CONTAINS_ANY:
            if not isinstance(expected, list) or not all(isinstance(item, str) for item in expected):
                return RuleOutcome.ERROR, actual
            text = str(actual)
            return (
                RuleOutcome.VIOLATED if any(item in text for item in expected) else RuleOutcome.PASSED,
                actual,
            )
        if condition.operator in (RuleOperator.BEFORE, RuleOperator.AFTER):
            try:
                actual_dt = self._as_datetime(actual)
                expected_dt = self._resolve_expected_datetime(expected, material, request_context)
            except (TypeError, ValueError):
                return RuleOutcome.ERROR, actual
            passed = (
                actual_dt < expected_dt
                if condition.operator == RuleOperator.BEFORE
                else actual_dt > expected_dt
            )
            return (RuleOutcome.PASSED if passed else RuleOutcome.VIOLATED), actual
        if condition.operator == RuleOperator.REGEX:
            if not isinstance(expected, str):
                return RuleOutcome.ERROR, actual
            try:
                passed = re.fullmatch(expected, str(actual)) is not None
            except re.error:
                return RuleOutcome.ERROR, actual
            return (RuleOutcome.PASSED if passed else RuleOutcome.VIOLATED), actual
        return RuleOutcome.ERROR, actual

    @staticmethod
    def _single_rule_evaluation(
        rule: AuditRuleRecord,
        outcome: RuleOutcome,
        *,
        reason: str,
        evidence_spans: list[str] | None = None,
        missing_fields: list[str] | None = None,
        confidence: float | None = None,
        evaluator: str,
        error_code: str | None = None,
    ) -> RuleEvaluation:
        return RuleEvaluation(
            rule_id=rule.rule_id,
            rule_name=rule.rule_name,
            rule_kind=rule.rule_kind,
            severity=rule.severity,
            outcome=outcome,
            reason=reason,
            evidence_spans=evidence_spans or [],
            missing_fields=missing_fields or [],
            confidence=confidence,
            evaluator=evaluator,
            error_code=error_code,
        )

    def _rule_evaluations(
        self,
        rules: list[AuditRuleRecord],
        outcome: RuleOutcome,
        *,
        reason: str,
        missing_fields: list[str] | None = None,
        evaluator: str,
        error_code: str | None = None,
    ) -> list[RuleEvaluation]:
        return [
            self._single_rule_evaluation(
                rule,
                outcome,
                reason=reason,
                missing_fields=missing_fields,
                evaluator=evaluator,
                error_code=error_code,
            )
            for rule in rules
        ]

    @staticmethod
    def _summarize(
        evaluations: list[RuleEvaluation],
        structured_rules: list[AuditRuleRecord],
        semantic_rules: list[AuditRuleRecord],
    ) -> AuditSummary:
        summary = AuditSummary(
            applicable_rule_count=len(structured_rules) + len(semantic_rules),
            structured_rule_count=len(structured_rules),
            semantic_rule_count=len(semantic_rules),
            checked_rule_count=len(evaluations),
        )
        for evaluation in evaluations:
            if evaluation.outcome == RuleOutcome.PASSED:
                summary.passed_rule_count += 1
            elif evaluation.outcome == RuleOutcome.VIOLATED:
                if evaluation.severity == RuleSeverity.BLOCKING:
                    summary.blocking_violation_count += 1
                else:
                    summary.warning_violation_count += 1
            elif evaluation.outcome == RuleOutcome.UNCERTAIN:
                summary.uncertain_rule_count += 1
            elif evaluation.outcome == RuleOutcome.MISSING:
                summary.missing_rule_count += 1
            elif evaluation.outcome == RuleOutcome.ERROR:
                summary.error_rule_count += 1
        return summary

    def _resolve_field(
        self,
        path: str,
        material: MaterialRecord,
        request_context: dict[str, Any],
    ) -> Any:
        parts = path.split(".")
        if not parts:
            raise ValueError("empty field path")
        if parts[0] == "material":
            if len(parts) == 1:
                raise ValueError("material root is not a field")
            name = parts[1]
            allowed = {
                "asset_id",
                "title",
                "platform",
                "source_status",
                "content",
                "metadata",
                "version",
                "updated_at",
            }
            if name not in allowed:
                raise ValueError(f"unsupported material field: {name}")
            value: Any = getattr(material, name)
            for part in parts[2:]:
                if not isinstance(value, dict) or part not in value:
                    return MISSING
                value = value[part]
            return value
        if parts[0] == "request":
            if len(parts) != 2 or parts[1] not in request_context:
                raise ValueError("unsupported request field")
            return request_context[parts[1]]
        raise ValueError("unsupported field root")

    def _resolve_expected_datetime(
        self,
        expected: Any,
        material: MaterialRecord,
        request_context: dict[str, Any],
    ) -> datetime:
        if isinstance(expected, str) and expected.startswith("field:"):
            value = self._resolve_field(expected[6:], material, request_context)
            if value is MISSING:
                raise ValueError("comparison field missing")
            return self._as_datetime(value)
        return self._as_datetime(expected)

    @staticmethod
    def _as_datetime(value: Any) -> datetime:
        if isinstance(value, datetime):
            parsed = value
        elif isinstance(value, str):
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        else:
            raise TypeError("not a datetime")
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError("datetime must be timezone-aware")
        return parsed.astimezone(UTC)

    @staticmethod
    def _condition_evidence(condition, actual: Any) -> str | None:
        if actual is MISSING or actual is None:
            return None
        if condition.operator == RuleOperator.CONTAINS_ANY and isinstance(condition.expected_value, list):
            text = str(actual)
            for keyword in condition.expected_value:
                pos = text.find(str(keyword))
                if pos >= 0:
                    start = max(0, pos - 40)
                    return text[start : start + 200]
        return str(actual)[:200]


class ScheduleService:
    def __init__(self, repository: ScheduleRepository):
        self.repository = repository

    def query(self, payload: ScheduleQueryInput, context: ScheduleQueryContext) -> ScheduleQueryResult:
        try:
            requested_local, requested_utc = normalize_datetime(payload.requested_at, payload.timezone)
            search_until = validate_search_window(
                requested_local,
                payload.search_until,
                payload.timezone,
            )
        except ValueError as exc:
            return ScheduleQueryResult(
                status=ScheduleQueryStatus.INVALID_ARGUMENT,
                error=ToolError(code="INVALID_REQUESTED_AT", message=str(exc)),
                meta={"contract_version": "0.2"},
            )

        start_key = utc_key(requested_utc)
        end_key = utc_key(search_until)
        try:
            occupancies = self.repository.list_active_between(
                context.scope_id,
                start_key,
                end_key,
                exclude_idempotency_key=context.idempotency_key,
                occupancy_minutes=context.occupancy_minutes,
            )
        except sqlite3.Error:
            return ScheduleQueryResult(
                status=ScheduleQueryStatus.STORAGE_ERROR,
                requested_at=requested_local,
                requested_at_utc=requested_utc,
                timezone=payload.timezone,
                scope_id=context.scope_id,
                error=ToolError(
                    code="SCHEDULE_STORAGE_UNAVAILABLE",
                    message="排期数据库暂时不可用",
                    retryable=True,
                ),
                meta={"contract_version": "0.2"},
            )

        conflicts = [
            self._to_conflict(item)
            for item in occupancies
            if intervals_overlap(
                requested_utc,
                context.occupancy_minutes,
                item.publish_at_utc,
                item.duration_minutes,
            )
        ]
        if not conflicts:
            return ScheduleQueryResult(
                status=ScheduleQueryStatus.AVAILABLE,
                requested_at=requested_local,
                requested_at_utc=requested_utc,
                timezone=payload.timezone,
                scope_id=context.scope_id,
                meta=self._meta(context, search_until),
            )

        candidates = generate_grid_candidates(
            requested_local,
            search_until,
            context.slot_step_minutes,
        )
        alternatives = [
            AlternativeSlot(
                publish_at=candidate,
                publish_at_utc=candidate.astimezone(UTC),
                occupied_until=candidate + timedelta(minutes=context.occupancy_minutes),
                occupied_until_utc=(
                    candidate.astimezone(UTC)
                    + timedelta(minutes=context.occupancy_minutes)
                ),
                duration_minutes=context.occupancy_minutes,
                timezone=payload.timezone,
                requires_confirmation=True,
            )
            for candidate in candidates
            if not any(
                intervals_overlap(
                    candidate.astimezone(UTC),
                    context.occupancy_minutes,
                    item.publish_at_utc,
                    item.duration_minutes,
                )
                for item in occupancies
            )
        ][: context.max_alternatives]

        status = (
            ScheduleQueryStatus.CONFLICT_WITH_ALTERNATIVE
            if alternatives
            else ScheduleQueryStatus.NO_FEASIBLE_SLOT
        )
        return ScheduleQueryResult(
            status=status,
            requested_at=requested_local,
            requested_at_utc=requested_utc,
            timezone=payload.timezone,
            scope_id=context.scope_id,
            conflicts=conflicts,
            alternatives=alternatives,
            meta=self._meta(context, search_until),
        )

    @staticmethod
    def _to_conflict(item: OccupancyRow) -> ConflictRecord:
        return ConflictRecord(
            record_type=item.record_type,
            record_id=item.record_id,
            asset_id=item.asset_id,
            publish_at=item.publish_at,
            publish_at_utc=item.publish_at_utc,
            occupied_until=item.publish_at + timedelta(minutes=item.duration_minutes),
            occupied_until_utc=item.publish_at_utc
            + timedelta(minutes=item.duration_minutes),
            duration_minutes=item.duration_minutes,
            status=item.status,
            occupancy=item.occupancy,
        )

    @staticmethod
    def _meta(context: ScheduleQueryContext, search_until: datetime) -> dict[str, Any]:
        return {
            "contract_version": "0.2",
            "query_mode": "interval_overlap",
            "occupancy_minutes": context.occupancy_minutes,
            "slot_step_minutes": context.slot_step_minutes,
            "max_alternatives": context.max_alternatives,
            "search_until": search_until.isoformat(),
        }


class DraftService:
    def __init__(self, repository: DraftRepository):
        self.repository = repository

    def save(
        self,
        payload: SaveDraftInput,
        context: SaveDraftContext,
        *,
        fail_on_save: bool = False,
    ) -> SaveDraftResult:
        if context.audit_status not in (AuditStatus.PASS, AuditStatus.WARNING):
            return SaveDraftResult(
                status=DraftSaveStatus.REJECTED,
                error=ToolError(
                    code="AUDIT_PRECHECK_FAILED",
                    message="审核尚未通过或存在阻断",
                ),
                meta={"contract_version": "0.2"},
            )
        if context.schedule_check_status not in (
            ScheduleQueryStatus.AVAILABLE,
            ScheduleQueryStatus.CONFLICT_WITH_ALTERNATIVE,
        ):
            return SaveDraftResult(
                status=DraftSaveStatus.REJECTED,
                error=ToolError(
                    code="SCHEDULE_CHECK_REQUIRED",
                    message="时段查询尚未得到可保存结果",
                ),
                meta={"contract_version": "0.2"},
            )

        try:
            proposed_local, proposed_utc = normalize_datetime(payload.proposed_at, payload.timezone)
            requested_utc = context.requested_at.astimezone(UTC)
            allowed_alternatives = {
                normalize_datetime(value, payload.timezone)[1]
                for value in context.allowed_alternatives
            }
        except ValueError as exc:
            return SaveDraftResult(
                status=DraftSaveStatus.INVALID_ARGUMENT,
                error=ToolError(code="INVALID_PROPOSED_AT", message=str(exc)),
                meta={"contract_version": "0.2"},
            )

        if proposed_utc != requested_utc and proposed_utc not in allowed_alternatives:
            return SaveDraftResult(
                status=DraftSaveStatus.REJECTED,
                error=ToolError(
                    code="PROPOSED_TIME_NOT_ALLOWED",
                    message="建议开始时间不是原请求或本次查询返回的候选",
                ),
                meta={"contract_version": "0.2"},
            )

        reasons: list[ConfirmationReason] = []
        if proposed_utc != requested_utc:
            reasons.append(ConfirmationReason.ALTERNATIVE_TIME)
        if context.audit_status == AuditStatus.WARNING:
            reasons.append(ConfirmationReason.RULE_WARNING)
        requires_confirmation = bool(reasons)
        draft_status = (
            DraftStatus.PENDING_CONFIRMATION
            if requires_confirmation
            else DraftStatus.READY
        )

        try:
            save_status, draft = self.repository.save(
                idempotency_key=context.idempotency_key,
                asset_id=payload.asset_id,
                scope_id=context.scope_id,
                requested_at=context.requested_at,
                requested_at_utc=requested_utc,
                proposed_at=proposed_local,
                proposed_at_utc=proposed_utc,
                duration_minutes=context.duration_minutes,
                timezone=payload.timezone,
                audit_status=context.audit_status,
                risk_rule_ids=context.risk_rule_ids,
                risk_reasons=context.risk_reasons,
                draft_status=draft_status,
                requires_confirmation=requires_confirmation,
                confirmation_reasons=reasons,
                fail_on_save=fail_on_save,
            )
        except DraftSlotConflictError:
            return SaveDraftResult(
                status=DraftSaveStatus.REJECTED,
                error=ToolError(
                    code="SLOT_ALREADY_OCCUPIED",
                    message="目标时段已被其他有效排期占用",
                ),
                meta={"contract_version": "0.2"},
            )
        except sqlite3.Error:
            return SaveDraftResult(
                status=DraftSaveStatus.STORAGE_ERROR,
                error=ToolError(
                    code="DRAFT_STORAGE_UNAVAILABLE",
                    message="草稿保存失败",
                    retryable=True,
                ),
                meta={"contract_version": "0.2"},
            )

        return SaveDraftResult(
            status=save_status,
            draft=draft,
            meta={
                "contract_version": "0.2",
                "idempotency_key_hash": idempotency_digest(context.idempotency_key),
                "save_mode": "transactional_draft_only",
                "occupancy_minutes": context.duration_minutes,
            },
        )


def idempotency_digest(key: str) -> str:
    return "sha256:" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def build_idempotency_key(
    workspace_id: str,
    scope_id: str,
    asset_id: str,
    requested_at_utc: datetime,
) -> str:
    raw = "|".join(
        [
            workspace_id.strip().lower(),
            scope_id.strip().lower(),
            asset_id.strip().lower(),
            utc_key(requested_at_utc),
            "CREATE_SCHEDULE_DRAFT",
        ]
    )
    return "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()
