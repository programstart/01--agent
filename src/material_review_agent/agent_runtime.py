from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from .config import Settings
from .db import Database
from .model_adapter import ModelAdapter
from .models import (
    AgentAction,
    AgentDecision,
    AgentMessage,
    AgentMessageRole,
    AuditCheckStatus,
    AuditStatus,
    ConfirmationReason,
    DraftSaveStatus,
    DraftStatus,
    DraftView,
    MaterialReadResult,
    MaterialReadStatus,
    PublicationStatus,
    RuleOutcome,
    RunReport,
    RunRequest,
    RunState,
    RunStatus,
    SaveDraftResult,
    ScheduleQueryResult,
    ScheduleQueryStatus,
    ToolCallRecord,
    ToolError,
    ToolName,
)
from .repositories import (
    DraftRepository,
    MaterialRepository,
    RuleRepository,
    ScheduleRepository,
)
from .services import (
    ASSET_ID_PATTERN,
    DraftService,
    MaterialService,
    RuleEngine,
    ScheduleService,
    build_idempotency_key,
)
from .semantic_rules import SemanticRuleEvaluator
from .time_utils import normalize_datetime, validate_search_window
from .tool_executor import ToolExecutor, ToolOutcome, ToolPreconditionError
from .tracing import TraceRecorder


@dataclass(slots=True)
class Services:
    materials: MaterialService
    rules: RuleEngine
    schedules: ScheduleService
    drafts: DraftService
    draft_repository: DraftRepository


class AgentGraphState(TypedDict, total=False):
    run_state: RunState
    decision: AgentDecision | None
    final_report: RunReport | None


def build_services(database: Database, semantic_evaluator: SemanticRuleEvaluator) -> Services:
    material_repository = MaterialRepository(database)
    rule_repository = RuleRepository(database)
    schedule_repository = ScheduleRepository(database)
    draft_repository = DraftRepository(database)
    return Services(
        materials=MaterialService(material_repository),
        rules=RuleEngine(material_repository, rule_repository, semantic_evaluator),
        schedules=ScheduleService(schedule_repository),
        drafts=DraftService(draft_repository),
        draft_repository=draft_repository,
    )


class AgentRuntime:
    def __init__(
        self,
        settings: Settings,
        services: Services,
        model: ModelAdapter,
    ):
        self.settings = settings
        self.services = services
        self.model = model

    def run(
        self,
        *,
        asset_id: str,
        requested_at: str | datetime,
        timezone_name: str,
        search_until: str | datetime | None = None,
        simulate_save_failure: bool = False,
    ) -> RunReport:
        state, tracer = self._build_initial_state(
            asset_id=asset_id,
            requested_at=requested_at,
            timezone_name=timezone_name,
            search_until=search_until,
        )
        try:
            graph = self._build_graph(
                tracer,
                simulate_save_failure=simulate_save_failure,
            )
            result = graph.invoke(
                {
                    "run_state": state,
                    "decision": None,
                    "final_report": None,
                },
                {"recursion_limit": self.settings.max_steps * 3 + 6},
            )
            report = result.get("final_report")
            if report is None:
                raise RuntimeError("LangGraph 未生成最终报告")
            return report
        except Exception as exc:
            tracer.record(
                "runtime_exception",
                error_type=type(exc).__name__,
                message=str(exc)[:300],
            )
            state.run_status = RunStatus.FAILED
            state.unfinished_reason = "Agent 运行时发生未恢复错误"
            trace_path = tracer.write()
            return self._build_report(
                state,
                trace_path=trace_path,
                forced_status=RunStatus.FAILED,
            )

    def _build_initial_state(
        self,
        *,
        asset_id: str,
        requested_at: str | datetime,
        timezone_name: str,
        search_until: str | datetime | None,
    ) -> tuple[RunState, TraceRecorder]:
        normalized_id = asset_id.strip()
        if not ASSET_ID_PATTERN.fullmatch(normalized_id):
            raise ValueError("素材 ID 格式不合法")
        requested_local, requested_utc = normalize_datetime(requested_at, timezone_name)
        boundary_local = None
        if search_until is not None:
            boundary_local, _ = normalize_datetime(search_until, timezone_name)
        boundary_local = validate_search_window(
            requested_local,
            boundary_local,
            timezone_name,
        )

        run_id = f"RUN-{uuid.uuid4().hex[:12].upper()}"
        idempotency_key = build_idempotency_key(
            self.settings.workspace_id,
            self.settings.scope_id,
            normalized_id,
            requested_utc,
        )
        request = RunRequest(
            run_id=run_id,
            workspace_id=self.settings.workspace_id,
            scope_id=self.settings.scope_id,
            asset_id=normalized_id,
            requested_at=requested_local,
            requested_at_utc=requested_utc,
            timezone=timezone_name,
            search_until=boundary_local,
            idempotency_key=idempotency_key,
        )
        initial_message = AgentMessage(
            role=AgentMessageRole.USER,
            content=(
                f"审核素材 {normalized_id}，请求发布时间为 "
                f"{requested_local.isoformat()}，只保存草稿。"
            ),
            created_at=datetime.now(UTC).replace(microsecond=0),
        )
        state = RunState(request=request, messages=[initial_message])
        tracer = TraceRecorder(run_id, self.settings.trace_dir)
        tracer.record(
            "request_normalized",
            asset_id=normalized_id,
            requested_at=requested_local.isoformat(),
            requested_at_utc=requested_utc.isoformat().replace("+00:00", "Z"),
            timezone=timezone_name,
            search_until=boundary_local.isoformat(),
            occupancy_minutes=self.settings.occupancy_minutes,
            slot_step_minutes=self.settings.slot_step_minutes,
            model_mode=self.model.name,
        )
        return state, tracer

    def _build_graph(
        self,
        tracer: TraceRecorder,
        *,
        simulate_save_failure: bool,
    ):
        executor = ToolExecutor(
            self.services.materials,
            self.services.rules,
            self.services.schedules,
            self.services.drafts,
            occupancy_minutes=self.settings.occupancy_minutes,
            slot_step_minutes=self.settings.slot_step_minutes,
            max_alternatives=self.settings.max_alternatives,
            fail_on_draft_save=simulate_save_failure,
        )

        graph = StateGraph(AgentGraphState)

        def prepare_run(graph_state: AgentGraphState) -> dict[str, Any]:
            state = graph_state["run_state"].model_copy(deep=True)
            existing = self.services.draft_repository.find_by_idempotency(
                state.request.idempotency_key
            )
            if existing is not None:
                state.existing_draft = existing
            tracer.record(
                "prepare_run",
                existing_draft_id=existing.draft_id if existing else None,
                idempotency_key_hash=_digest(state.request.idempotency_key),
            )
            return {"run_state": state, "decision": None}

        def model_decide(graph_state: AgentGraphState) -> dict[str, Any]:
            state = graph_state["run_state"].model_copy(deep=True)
            if state.run_status != RunStatus.RUNNING:
                return {"run_state": state, "decision": None}
            if state.step_count >= self.settings.max_steps:
                state.run_status = RunStatus.INCOMPLETE
                state.unfinished_reason = "达到模型步骤上限"
                tracer.record("guard_stop", reason=state.unfinished_reason)
                return {"run_state": state, "decision": None}

            allowed = self._allowed_actions(state)
            state.step_count += 1
            try:
                decision = self.model.decide(state, allowed)
            except Exception as exc:
                state.run_status = RunStatus.FAILED
                state.unfinished_reason = "模型调用失败"
                tracer.record(
                    "model_error",
                    error_type=type(exc).__name__,
                    message=str(exc)[:300],
                )
                return {"run_state": state, "decision": None}

            action = AgentAction(decision.action)
            state.messages.append(
                AgentMessage(
                    role=AgentMessageRole.ASSISTANT,
                    content=decision.decision_basis,
                    tool_name=action.value if action != AgentAction.FINISH else None,
                    arguments=decision.arguments or None,
                    created_at=datetime.now(UTC).replace(microsecond=0),
                )
            )
            tracer.record(
                "model_decision",
                step=state.step_count,
                action=action.value,
                arguments=decision.arguments,
                decision_basis=decision.decision_basis,
                allowed_actions=[item.value for item in allowed],
            )
            if action != AgentAction.FINISH and action not in allowed:
                state.run_status = RunStatus.INCOMPLETE
                state.unfinished_reason = f"模型选择了当前不允许的动作: {action.value}"
                tracer.record("invalid_action", action=action.value)
            return {"run_state": state, "decision": decision}

        def execute_tool(graph_state: AgentGraphState) -> dict[str, Any]:
            state = graph_state["run_state"].model_copy(deep=True)
            decision = graph_state.get("decision")
            if decision is None:
                state.run_status = RunStatus.INCOMPLETE
                state.unfinished_reason = "没有可执行的模型动作"
                return {"run_state": state}
            action = AgentAction(decision.action)
            if state.tool_call_count >= self.settings.max_tool_calls:
                state.run_status = RunStatus.INCOMPLETE
                state.unfinished_reason = "达到工具调用次数上限"
                tracer.record("guard_stop", reason=state.unfinished_reason)
                return {"run_state": state}

            if (
                action == AgentAction.READ_MATERIAL
                and state.material_result is not None
                and state.material_result.status == MaterialReadStatus.STORAGE_ERROR
            ):
                state.failed_retry_count += 1

            state.tool_call_count += 1
            try:
                outcome = executor.execute(action, decision.arguments, state)
            except ToolPreconditionError as exc:
                state.run_status = RunStatus.INCOMPLETE
                state.unfinished_reason = exc.message
                record = ToolCallRecord(
                    step=state.step_count,
                    tool_name=ToolName(action.value),
                    arguments=decision.arguments,
                    result_status="REJECTED",
                    summary={"message": exc.message},
                    error_code=exc.code,
                    decision_basis=decision.decision_basis,
                    created_at=datetime.now(UTC).replace(microsecond=0),
                )
                state.tool_history.append(record)
                tracer.record(
                    "tool_rejected",
                    tool=action.value,
                    arguments=decision.arguments,
                    error_code=exc.code,
                    message=exc.message,
                )
                return {"run_state": state}
            except Exception as exc:
                state.run_status = RunStatus.FAILED
                state.unfinished_reason = "工具运行发生未恢复错误"
                tracer.record(
                    "tool_runtime_error",
                    tool=action.value,
                    error_type=type(exc).__name__,
                    message=str(exc)[:300],
                )
                return {"run_state": state}

            self._apply_tool_outcome(state, outcome)
            state.tool_history.append(
                ToolCallRecord(
                    step=state.step_count,
                    tool_name=outcome.tool_name,
                    arguments=decision.arguments,
                    result_status=outcome.result_status,
                    summary=outcome.summary,
                    error_code=outcome.summary.get("error_code"),
                    decision_basis=decision.decision_basis,
                    created_at=datetime.now(UTC).replace(microsecond=0),
                )
            )
            state.messages.append(
                AgentMessage(
                    role=AgentMessageRole.TOOL,
                    content=json.dumps(outcome.summary, ensure_ascii=False),
                    tool_name=outcome.tool_name.value,
                    payload=outcome.summary,
                    created_at=datetime.now(UTC).replace(microsecond=0),
                )
            )
            action_signature = action.value + ":" + json.dumps(
                decision.arguments,
                ensure_ascii=False,
                sort_keys=True,
            )
            observation_signature = hashlib.sha256(
                json.dumps(outcome.summary, ensure_ascii=False, sort_keys=True).encode("utf-8")
            ).hexdigest()
            if (
                state.last_action_signature == action_signature
                and state.last_observation_signature == observation_signature
            ):
                state.run_status = RunStatus.INCOMPLETE
                state.unfinished_reason = "相同动作和相同结果重复出现，判定为无进展"
            state.last_action_signature = action_signature
            state.last_observation_signature = observation_signature
            tracer.record(
                "tool_execution",
                step=state.step_count,
                tool=outcome.tool_name.value,
                arguments=decision.arguments,
                result_status=outcome.result_status,
                summary=outcome.summary,
                model_budget={
                    "steps": state.step_count,
                    "tool_calls": state.tool_call_count,
                },
            )
            return {"run_state": state}

        def guard(graph_state: AgentGraphState) -> dict[str, Any]:
            state = graph_state["run_state"].model_copy(deep=True)
            if state.run_status in (RunStatus.INCOMPLETE, RunStatus.FAILED):
                tracer.record("guard_route", route="finalize", reason=state.unfinished_reason)
                return {"run_state": state}
            draft_succeeded = state.draft_result is not None and state.draft_result.status in (
                DraftSaveStatus.SAVED,
                DraftSaveStatus.EXISTING,
            )
            if state.tool_call_count >= self.settings.max_tool_calls:
                if draft_succeeded:
                    tracer.record("guard_route", route="finalize", reason="草稿已保存且达到工具预算")
                else:
                    state.run_status = RunStatus.INCOMPLETE
                    state.unfinished_reason = "达到工具调用次数上限"
                    tracer.record("guard_route", route="finalize", reason=state.unfinished_reason)
                return {"run_state": state}
            if state.step_count >= self.settings.max_steps:
                if draft_succeeded:
                    tracer.record("guard_route", route="finalize", reason="草稿已保存且达到模型预算")
                else:
                    state.run_status = RunStatus.INCOMPLETE
                    state.unfinished_reason = "达到模型步骤上限"
                    tracer.record("guard_route", route="finalize", reason=state.unfinished_reason)
                return {"run_state": state}
            tracer.record("guard_route", route="model_decide")
            return {"run_state": state}

        def finalize(graph_state: AgentGraphState) -> dict[str, Any]:
            state = graph_state["run_state"].model_copy(deep=True)
            report = self._build_report(state, trace_path=None)
            tracer.record(
                "finalize",
                run_status=_enum_value(report.run_status),
                draft_id=report.draft_id,
                draft_save_status=_enum_value(report.draft_save_status),
                publication_status=_enum_value(report.publication_status),
                unfinished_reason=report.unfinished_reason,
            )
            trace_path = tracer.write()
            report.trace_path = str(trace_path)
            return {"run_state": state, "final_report": report}

        def after_model(graph_state: AgentGraphState) -> str:
            decision = graph_state.get("decision")
            state = graph_state["run_state"]
            if state.run_status != RunStatus.RUNNING or decision is None:
                return "finalize"
            return "finalize" if decision.action == AgentAction.FINISH.value else "execute_tool"

        def after_guard(graph_state: AgentGraphState) -> str:
            state = graph_state["run_state"]
            if state.run_status != RunStatus.RUNNING:
                return "finalize"
            if state.step_count >= self.settings.max_steps or state.tool_call_count >= self.settings.max_tool_calls:
                return "finalize"
            return "model_decide"

        graph.add_node("prepare_run", prepare_run)
        graph.add_node("model_decide", model_decide)
        graph.add_node("execute_tool", execute_tool)
        graph.add_node("guard", guard)
        graph.add_node("finalize", finalize)
        graph.add_edge(START, "prepare_run")
        graph.add_edge("prepare_run", "model_decide")
        graph.add_conditional_edges(
            "model_decide",
            after_model,
            {"execute_tool": "execute_tool", "finalize": "finalize"},
        )
        graph.add_edge("execute_tool", "guard")
        graph.add_conditional_edges(
            "guard",
            after_guard,
            {"model_decide": "model_decide", "finalize": "finalize"},
        )
        graph.add_edge("finalize", END)
        return graph.compile()

    def _allowed_actions(self, state: RunState) -> list[AgentAction]:
        if state.draft_result is not None:
            return [AgentAction.FINISH]

        material = state.material_result
        if material is None:
            return [AgentAction.READ_MATERIAL]
        if material.status == MaterialReadStatus.STORAGE_ERROR and state.failed_retry_count < 1:
            return [AgentAction.READ_MATERIAL]
        if material.status != MaterialReadStatus.SUCCESS:
            return [AgentAction.FINISH]

        audit = state.audit_result
        if audit is None:
            return [AgentAction.CHECK_AUDIT_RULES, AgentAction.READ_MATERIAL]
        if audit.status not in (AuditCheckStatus.PASS, AuditCheckStatus.WARNING):
            return [AgentAction.FINISH]

        schedule = state.schedule_result
        if schedule is None:
            return [
                AgentAction.QUERY_SCHEDULE_SLOTS,
                AgentAction.CHECK_AUDIT_RULES,
                AgentAction.READ_MATERIAL,
            ]
        if schedule.status in (
            ScheduleQueryStatus.AVAILABLE,
            ScheduleQueryStatus.CONFLICT_WITH_ALTERNATIVE,
        ):
            return [
                AgentAction.SAVE_SCHEDULE_DRAFT,
                AgentAction.QUERY_SCHEDULE_SLOTS,
                AgentAction.CHECK_AUDIT_RULES,
                AgentAction.READ_MATERIAL,
            ]
        return [AgentAction.FINISH]

    @staticmethod
    def _apply_tool_outcome(state: RunState, outcome: ToolOutcome) -> None:
        result = outcome.result
        if isinstance(result, MaterialReadResult):
            state.material_result = result
        elif outcome.tool_name == ToolName.CHECK_AUDIT_RULES:
            state.audit_result = result
        elif isinstance(result, ScheduleQueryResult):
            state.schedule_result = result
        elif isinstance(result, SaveDraftResult):
            state.draft_result = result
            if result.draft:
                state.pending_items = list(result.draft.confirmation_reasons)

    def _build_report(
        self,
        state: RunState,
        *,
        trace_path: Any,
        forced_status: RunStatus | None = None,
    ) -> RunReport:
        audit_business_status = None
        audit = state.audit_result
        if audit and audit.status in (
            AuditCheckStatus.PASS,
            AuditCheckStatus.WARNING,
            AuditCheckStatus.NEEDS_INFO,
            AuditCheckStatus.NEEDS_REVIEW,
            AuditCheckStatus.BLOCKED,
        ):
            audit_business_status = AuditStatus(audit.status.value)

        draft = state.draft_result.draft if state.draft_result else state.existing_draft
        save_status = (
            state.draft_result.status
            if state.draft_result
            else (DraftSaveStatus.EXISTING if state.existing_draft else DraftSaveStatus.NOT_ATTEMPTED)
        )
        if forced_status is not None:
            run_status = forced_status
        elif state.draft_result and state.draft_result.status in (
            DraftSaveStatus.SAVED,
            DraftSaveStatus.EXISTING,
        ):
            run_status = RunStatus.COMPLETED
        elif state.existing_draft is not None and state.draft_result is None:
            run_status = RunStatus.COMPLETED
        elif state.run_status == RunStatus.FAILED:
            run_status = RunStatus.FAILED
        else:
            run_status = RunStatus.INCOMPLETE

        unfinished_reason = state.unfinished_reason
        if run_status == RunStatus.INCOMPLETE and not unfinished_reason:
            unfinished_reason = self._infer_unfinished_reason(state)

        risk_reasons = []
        if audit:
            risk_reasons.extend(
                item.reason
                for item in audit.rule_results
                if item.outcome in (
                    RuleOutcome.VIOLATED,
                    RuleOutcome.UNCERTAIN,
                    RuleOutcome.MISSING,
                    RuleOutcome.ERROR,
                )
            )
        if (
            state.material_result
            and state.material_result.status == MaterialReadStatus.INCOMPLETE
            and state.material_result.data_completeness
        ):
            risk_reasons.extend(
                f"素材缺少字段: {field}"
                for field in state.material_result.data_completeness.missing_fields
            )

        proposed_at = draft.proposed_at if draft else None
        if proposed_at is None and state.schedule_result:
            if state.schedule_result.status == ScheduleQueryStatus.AVAILABLE:
                proposed_at = state.schedule_result.requested_at
            elif state.schedule_result.alternatives:
                proposed_at = state.schedule_result.alternatives[0].publish_at

        return RunReport(
            run_id=state.request.run_id,
            asset_id=state.request.asset_id,
            audit_status=audit_business_status,
            risk_reasons=risk_reasons,
            requested_at=state.request.requested_at,
            proposed_at=proposed_at,
            occupancy_minutes=(
                draft.duration_minutes
                if draft
                else self.settings.occupancy_minutes
            ),
            requires_confirmation=draft.requires_confirmation if draft else False,
            confirmation_reasons=draft.confirmation_reasons if draft else [],
            draft_id=draft.draft_id if draft else None,
            draft_status=draft.draft_status if draft else None,
            draft_save_status=save_status,
            publication_status=PublicationStatus.NOT_PUBLISHED,
            run_status=run_status,
            unfinished_reason=unfinished_reason,
            trace_path=str(trace_path) if trace_path else None,
            tool_steps=state.tool_call_count,
            model_steps=state.step_count,
            model_mode=self.model.name,
        )

    @staticmethod
    def _infer_unfinished_reason(state: RunState) -> str:
        material = state.material_result
        if material is None:
            return "尚未读取素材"
        if material.status == MaterialReadStatus.NOT_FOUND:
            return "素材不存在，请核对素材 ID"
        if material.status == MaterialReadStatus.INCOMPLETE:
            missing = material.data_completeness.missing_fields if material.data_completeness else []
            return f"素材资料不足: {', '.join(missing)}"
        if material.status in (MaterialReadStatus.INVALID_ARGUMENT, MaterialReadStatus.STORAGE_ERROR):
            return "素材读取失败"

        audit = state.audit_result
        if audit is None:
            return "尚未完成审核规则检查"
        if audit.status == AuditCheckStatus.NEEDS_INFO:
            return "审核规则所需资料不足"
        if audit.status == AuditCheckStatus.NEEDS_REVIEW:
            return "存在需要人工复核的语义风险"
        if audit.status == AuditCheckStatus.BLOCKED:
            return "素材未通过阻断性审核规则"
        if audit.status == AuditCheckStatus.ERROR:
            return "审核规则执行失败"

        schedule = state.schedule_result
        if schedule is None:
            return "尚未完成时段查询"
        if schedule.status == ScheduleQueryStatus.NO_FEASIBLE_SLOT:
            return "目标时段冲突且没有可行替代时段"

        draft = state.draft_result
        if draft is None:
            return "模型在保存草稿前结束"
        if draft.error:
            return draft.error.message
        return "排期任务未完成"


def _digest(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def _enum_value(value: Any) -> Any:
    return value.value if hasattr(value, "value") else value
