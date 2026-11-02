from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC
from typing import Any

from pydantic import ValidationError

from .models import (
    AgentAction,
    AuditCheckInput,
    AuditCheckResult,
    AuditCheckStatus,
    MaterialReadInput,
    MaterialReadResult,
    MaterialReadStatus,
    RunState,
    SaveDraftContext,
    SaveDraftInput,
    SaveDraftResult,
    ScheduleQueryContext,
    ScheduleQueryInput,
    ScheduleQueryResult,
    ScheduleQueryStatus,
    ToolName,
    RuleOutcome,
)
from .services import DraftService, MaterialService, RuleEngine, ScheduleService
from .time_utils import normalize_datetime


class ToolPreconditionError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(slots=True)
class ToolOutcome:
    tool_name: ToolName
    result: MaterialReadResult | AuditCheckResult | ScheduleQueryResult | SaveDraftResult
    summary: dict[str, Any]
    result_status: str


class ToolExecutor:
    def __init__(
        self,
        material_service: MaterialService,
        rule_service: RuleEngine,
        schedule_service: ScheduleService,
        draft_service: DraftService,
        *,
        occupancy_minutes: int,
        slot_step_minutes: int,
        max_alternatives: int,
        fail_on_draft_save: bool = False,
    ):
        self.material_service = material_service
        self.rule_service = rule_service
        self.schedule_service = schedule_service
        self.draft_service = draft_service
        self.occupancy_minutes = occupancy_minutes
        self.slot_step_minutes = slot_step_minutes
        self.max_alternatives = max_alternatives
        self.fail_on_draft_save = fail_on_draft_save

    def execute(self, decision: AgentAction, arguments: dict[str, Any], state: RunState) -> ToolOutcome:
        try:
            if decision == AgentAction.READ_MATERIAL:
                return self._read_material(arguments, state)
            if decision == AgentAction.CHECK_AUDIT_RULES:
                return self._check_rules(arguments, state)
            if decision == AgentAction.QUERY_SCHEDULE_SLOTS:
                return self._query_schedule(arguments, state)
            if decision == AgentAction.SAVE_SCHEDULE_DRAFT:
                return self._save_draft(arguments, state)
        except ValidationError as exc:
            raise ToolPreconditionError("INVALID_TOOL_ARGUMENTS", _validation_message(exc)) from exc
        raise ToolPreconditionError("INVALID_ACTION", "不支持的 Agent 动作")

    def _read_material(self, arguments: dict[str, Any], state: RunState) -> ToolOutcome:
        payload = MaterialReadInput.model_validate(arguments)
        self._ensure_same_asset(payload.asset_id, state.request.asset_id)
        result = self.material_service.read(payload)
        return ToolOutcome(
            tool_name=ToolName.READ_MATERIAL,
            result=result,
            summary={
                "status": result.status.value,
                "asset_id": result.material.asset_id if result.material else payload.asset_id,
                "missing_fields": result.data_completeness.missing_fields
                if result.data_completeness
                else [],
                "content_truncated": result.material.content_truncated if result.material else False,
                "error_code": result.error.code if result.error else None,
            },
            result_status=result.status.value,
        )

    def _check_rules(self, arguments: dict[str, Any], state: RunState) -> ToolOutcome:
        if state.material_result is None or state.material_result.status != MaterialReadStatus.SUCCESS:
            raise ToolPreconditionError("MATERIAL_NOT_READY", "必须先成功读取完整素材")

        payload = AuditCheckInput.model_validate(arguments)
        self._ensure_same_asset(payload.asset_id, state.request.asset_id)
        self._ensure_request_arguments(payload.requested_at, payload.timezone, state)
        result = self.rule_service.check(
            AuditCheckInput(
                asset_id=state.request.asset_id,
                requested_at=state.request.requested_at,
                timezone=state.request.timezone,
            )
        )
        return ToolOutcome(
            tool_name=ToolName.CHECK_AUDIT_RULES,
            result=result,
            summary={
                "status": result.status.value,
                "checked_rules": result.summary.checked_rule_count,
                "violations": [
                    {
                        "rule_id": rule.rule_id,
                        "rule_kind": rule.rule_kind.value,
                        "severity": rule.severity.value,
                        "reason": rule.reason,
                        "evidence_spans": rule.evidence_spans,
                    }
                    for rule in result.rule_results
                    if rule.outcome == RuleOutcome.VIOLATED
                ],
                "uncertain_rules": [
                    rule.rule_id
                    for rule in result.rule_results
                    if rule.outcome == RuleOutcome.UNCERTAIN
                ],
                "missing_fields": [
                    field
                    for rule in result.rule_results
                    for field in rule.missing_fields
                ],
                "error_code": result.error.code if result.error else None,
            },
            result_status=result.status.value,
        )

    def _query_schedule(self, arguments: dict[str, Any], state: RunState) -> ToolOutcome:
        if state.audit_result is None or state.audit_result.status not in (
            AuditCheckStatus.PASS,
            AuditCheckStatus.WARNING,
        ):
            raise ToolPreconditionError("AUDIT_NOT_READY", "审核未通过，不能查询排期")

        payload = ScheduleQueryInput.model_validate(arguments)
        self._ensure_same_asset(payload.asset_id, state.request.asset_id)
        self._ensure_request_arguments(payload.requested_at, payload.timezone, state)
        context = ScheduleQueryContext(
            idempotency_key=state.request.idempotency_key,
            scope_id=state.request.scope_id,
            occupancy_minutes=self.occupancy_minutes,
            slot_step_minutes=self.slot_step_minutes,
            max_alternatives=self.max_alternatives,
        )
        result = self.schedule_service.query(
            ScheduleQueryInput(
                asset_id=state.request.asset_id,
                requested_at=state.request.requested_at,
                timezone=state.request.timezone,
                search_until=state.request.search_until,
            ),
            context,
        )
        return ToolOutcome(
            tool_name=ToolName.QUERY_SCHEDULE_SLOTS,
            result=result,
            summary={
                "status": result.status.value,
                "conflict_ids": [item.record_id for item in result.conflicts],
                "alternative_count": len(result.alternatives),
                "alternative_starts": [
                    item.publish_at.isoformat() for item in result.alternatives
                ],
                "occupancy_minutes": result.meta.get("occupancy_minutes"),
                "error_code": result.error.code if result.error else None,
            },
            result_status=result.status.value,
        )

    def _save_draft(self, arguments: dict[str, Any], state: RunState) -> ToolOutcome:
        if state.audit_result is None or state.audit_result.status not in (
            AuditCheckStatus.PASS,
            AuditCheckStatus.WARNING,
        ):
            raise ToolPreconditionError("AUDIT_NOT_READY", "审核未完成，不能保存草稿")
        if state.schedule_result is None:
            raise ToolPreconditionError("SCHEDULE_CHECK_REQUIRED", "必须先查询目标时段")

        payload = SaveDraftInput.model_validate(arguments)
        self._ensure_same_asset(payload.asset_id, state.request.asset_id)
        if payload.timezone != state.request.timezone:
            raise ToolPreconditionError("INVALID_TIMEZONE", "保存草稿的时区与请求不一致")
        _, proposed_utc = normalize_datetime(payload.proposed_at, payload.timezone)
        requested_utc = state.request.requested_at_utc.astimezone(UTC)
        allowed = [state.request.requested_at_utc]
        allowed.extend(item.publish_at_utc for item in state.schedule_result.alternatives)
        if proposed_utc not in {value.astimezone(UTC) for value in allowed}:
            raise ToolPreconditionError(
                "PROPOSED_TIME_NOT_ALLOWED",
                "建议开始时间不是原请求或最近一次查询返回的候选",
            )

        context = SaveDraftContext(
            run_id=state.request.run_id,
            idempotency_key=state.request.idempotency_key,
            scope_id=state.request.scope_id,
            requested_at=state.request.requested_at,
            duration_minutes=self.occupancy_minutes,
            audit_status=state.audit_result.status,
            risk_rule_ids=[
                item.rule_id
                for item in state.audit_result.rule_results
                if item.outcome == RuleOutcome.VIOLATED
            ],
            risk_reasons=[
                item.reason
                for item in state.audit_result.rule_results
                if item.outcome == RuleOutcome.VIOLATED
            ],
            schedule_check_status=state.schedule_result.status,
            allowed_alternatives=[
                item.publish_at_utc for item in state.schedule_result.alternatives
            ],
        )
        result = self.draft_service.save(
            SaveDraftInput(
                asset_id=state.request.asset_id,
                proposed_at=proposed_utc,
                timezone=state.request.timezone,
            ),
            context,
            fail_on_save=self.fail_on_draft_save,
        )
        return ToolOutcome(
            tool_name=ToolName.SAVE_SCHEDULE_DRAFT,
            result=result,
            summary={
                "status": result.status.value,
                "draft_id": result.draft.draft_id if result.draft else None,
                "draft_status": result.draft.draft_status.value if result.draft else None,
                "duration_minutes": result.draft.duration_minutes if result.draft else None,
                "requires_confirmation": result.draft.requires_confirmation if result.draft else None,
                "publication_status": result.draft.publication_status.value if result.draft else None,
                "error_code": result.error.code if result.error else None,
            },
            result_status=result.status.value,
        )

    @staticmethod
    def _ensure_same_asset(requested: str, current: str) -> None:
        if requested.strip().casefold() != current.strip().casefold():
            raise ToolPreconditionError("ASSET_ID_MISMATCH", "工具参数中的素材 ID 与当前请求不一致")

    @staticmethod
    def _ensure_request_arguments(value, timezone: str, state: RunState) -> None:
        if timezone != state.request.timezone:
            raise ToolPreconditionError("INVALID_TIMEZONE", "工具时区与当前请求不一致")
        _, utc_value = normalize_datetime(value, timezone)
        if utc_value != state.request.requested_at_utc.astimezone(UTC):
            raise ToolPreconditionError("REQUEST_TIME_MISMATCH", "工具时间与当前请求不一致")


def _validation_message(exc: ValidationError) -> str:
    first = exc.errors()[0]
    location = ".".join(str(item) for item in first.get("loc", ()))
    return f"{location}: {first.get('msg', '参数不合法')}"
