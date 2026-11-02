from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Protocol

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from .config import Settings
from .models import (
    AgentAction,
    AgentDecision,
    DraftSaveStatus,
    MaterialReadStatus,
    RunState,
    ScheduleQueryStatus,
    AuditCheckStatus,
)


class ModelAdapter(Protocol):
    name: str

    def decide(self, state: RunState, allowed_actions: list[AgentAction]) -> AgentDecision:
        ...


class MockModel:
    """Deterministic model used for tests and offline demonstrations.

    It chooses actions from the latest structured observations. It validates the
    workflow, but it is not evidence of a real model's decision quality.
    """

    name = "mock"

    def decide(self, state: RunState, allowed_actions: list[AgentAction]) -> AgentDecision:
        material = state.material_result
        if material is None:
            return AgentDecision(
                action=AgentAction.READ_MATERIAL.value,
                arguments={"asset_id": state.request.asset_id},
                decision_basis="尚未读取素材，先获取审核所需数据。",
            )
        if material.status == MaterialReadStatus.STORAGE_ERROR and state.failed_retry_count < 1:
            return AgentDecision(
                action=AgentAction.READ_MATERIAL.value,
                arguments={"asset_id": state.request.asset_id},
                decision_basis="素材读取失败，执行一次有限重试。",
            )
        if material.status != MaterialReadStatus.SUCCESS:
            return AgentDecision(
                action=AgentAction.FINISH.value,
                arguments={},
                decision_basis="素材无法继续审核，停止排期。",
            )

        audit = state.audit_result
        if audit is None:
            return AgentDecision(
                action=AgentAction.CHECK_AUDIT_RULES.value,
                arguments={
                    "asset_id": state.request.asset_id,
                    "requested_at": state.request.requested_at.isoformat(),
                    "timezone": state.request.timezone,
                },
                decision_basis="素材读取成功，继续检查当前审核规则。",
            )
        if audit.status not in (AuditCheckStatus.PASS, AuditCheckStatus.WARNING):
            return AgentDecision(
                action=AgentAction.FINISH.value,
                arguments={},
                decision_basis="审核结果不允许继续排期。",
            )

        schedule = state.schedule_result
        if schedule is None:
            return AgentDecision(
                action=AgentAction.QUERY_SCHEDULE_SLOTS.value,
                arguments={
                    "asset_id": state.request.asset_id,
                    "requested_at": state.request.requested_at.isoformat(),
                    "timezone": state.request.timezone,
                    **(
                        {"search_until": state.request.search_until.isoformat()}
                        if state.request.search_until
                        else {}
                    ),
                },
                decision_basis="审核允许继续，查询目标时段是否冲突。",
            )
        if schedule.status not in (
            ScheduleQueryStatus.AVAILABLE,
            ScheduleQueryStatus.CONFLICT_WITH_ALTERNATIVE,
        ):
            return AgentDecision(
                action=AgentAction.FINISH.value,
                arguments={},
                decision_basis="当前没有可保存的排期时段。",
            )

        if state.draft_result is not None:
            return AgentDecision(
                action=AgentAction.FINISH.value,
                arguments={},
                decision_basis="草稿保存流程已完成。",
            )

        proposed_at = state.request.requested_at
        if schedule.status == ScheduleQueryStatus.CONFLICT_WITH_ALTERNATIVE:
            proposed_at = schedule.alternatives[0].publish_at
        return AgentDecision(
            action=AgentAction.SAVE_SCHEDULE_DRAFT.value,
            arguments={
                "asset_id": state.request.asset_id,
                "proposed_at": proposed_at.isoformat(),
                "timezone": state.request.timezone,
            },
            decision_basis=(
                "使用用户原始时段保存草稿。"
                if proposed_at == state.request.requested_at
                else "原时段冲突，保存第一个建议时段并标记待确认。"
            ),
        )


class LiveModel:
    """LangChain ChatOpenAI adapter selected when live mode is requested."""

    name = "live"

    def __init__(self, settings: Settings):
        if not settings.model_name:
            raise ValueError("live 模式需要设置 OPENAI_MODEL")
        if not os.getenv("OPENAI_API_KEY"):
            raise ValueError("live 模式需要设置 OPENAI_API_KEY")
        kwargs = {
            "model": settings.model_name,
            "temperature": 0,
        }
        if settings.model_base_url:
            kwargs["base_url"] = settings.model_base_url
        self.model = ChatOpenAI(**kwargs)
        self.structured_model = self.model.with_structured_output(AgentDecision)
        self.max_steps = settings.max_steps
        self.max_tool_calls = settings.max_tool_calls
        self.occupancy_minutes = settings.occupancy_minutes
        self.slot_step_minutes = settings.slot_step_minutes

    def decide(self, state: RunState, allowed_actions: list[AgentAction]) -> AgentDecision:
        summary = self._state_summary(state, allowed_actions)
        system = SystemMessage(
            content=(
                "你是素材审核与发布排期 Agent。"
                "你只能从 allowed_actions 中选择一个动作，或选择 finish。"
                "必须根据工具返回的 observation 决定下一步。"
                "不要自行宣称素材审核通过、排期确认或已经发布。"
                "不要输出内部思维过程，只给出不超过 300 字的 decision_basis。"
                "只能调用当前允许的业务工具；不要生成数据库操作。"
                "budget 中 remaining_* 大于 0 时仍可继续调用工具，已用次数为 0 不代表没有预算。"
                "正常请求必须至少完成素材读取、规则检查和时段查询；"
                "只有草稿已保存/已存在，或遇到不可继续的业务状态、工具错误时才能选择 finish。"
                "每个发布请求默认占用一个连续时段，时长和替代步频由运行时配置决定。"
                "工具参数必须严格使用对应 schema。"
            )
        )
        human = HumanMessage(content=json.dumps(summary, ensure_ascii=False))
        result = self.structured_model.invoke([system, human])
        if isinstance(result, AgentDecision):
            return result
        return AgentDecision.model_validate(result)

    def _state_summary(self, state: RunState, allowed_actions: list[AgentAction]) -> dict:
        request = state.request
        summary: dict = {
            "goal": "审核素材并保存无冲突的排期草稿；不执行真实发布。",
            "allowed_actions": [action.value for action in allowed_actions],
            "request": {
                "asset_id": request.asset_id,
                "requested_at": request.requested_at.isoformat(),
                "timezone": request.timezone,
            },
            "budget": {
                "model_steps_used": state.step_count,
                "model_steps_remaining": max(0, self.max_steps - state.step_count),
                "tool_calls_used": state.tool_call_count,
                "tool_calls_remaining": max(0, self.max_tool_calls - state.tool_call_count),
            },
            "schedule_policy": {
                "occupancy_minutes": self.occupancy_minutes,
                "slot_step_minutes": self.slot_step_minutes,
                "conflict_rule": "half_open_interval_overlap",
            },
            "tool_argument_schemas": {
                "read_material": {"asset_id": "string"},
                "check_audit_rules": {
                    "asset_id": "string",
                    "requested_at": "RFC3339 string",
                    "timezone": "IANA timezone string",
                },
                "query_schedule_slots": {
                    "asset_id": "string",
                    "requested_at": "RFC3339 string",
                    "timezone": "IANA timezone string",
                    "search_until": "optional RFC3339 string",
                },
                "save_schedule_draft": {
                    "asset_id": "string",
                    "proposed_at": "RFC3339 string",
                    "timezone": "IANA timezone string",
                },
            },
        }
        if state.existing_draft:
            summary["existing_draft"] = {
                "draft_id": state.existing_draft.draft_id,
                "draft_status": state.existing_draft.draft_status.value,
                "publication_status": state.existing_draft.publication_status.value,
            }
        if state.material_result:
            material = state.material_result
            summary["material_observation"] = {
                "status": material.status.value,
                "missing_fields": material.data_completeness.missing_fields
                if material.data_completeness
                else [],
                "source_status": material.material.source_status.value
                if material.material and material.material.source_status
                else None,
                "title": material.material.title if material.material else None,
                "platform": material.material.platform if material.material else None,
            }
        if state.audit_result:
            audit = state.audit_result
            summary["audit_observation"] = {
                "status": audit.status.value,
                "rule_results": [
                    rule.model_dump(mode="json") for rule in audit.rule_results
                ],
                "summary": audit.summary.model_dump(mode="json"),
                "error": audit.error.model_dump(mode="json") if audit.error else None,
            }
        if state.schedule_result:
            schedule = state.schedule_result
            summary["schedule_observation"] = {
                "status": schedule.status.value,
                "requested_at": schedule.requested_at.isoformat() if schedule.requested_at else None,
                "conflicts": [item.model_dump(mode="json") for item in schedule.conflicts],
                "alternatives": [item.model_dump(mode="json") for item in schedule.alternatives],
            }
        if state.draft_result:
            summary["draft_observation"] = state.draft_result.model_dump(mode="json")
        return summary


def build_model(settings: Settings) -> ModelAdapter:
    if settings.model_mode == "mock":
        return MockModel()
    if settings.model_mode == "live":
        return LiveModel(settings)
    raise ValueError(f"不支持的模型模式: {settings.model_mode}")
