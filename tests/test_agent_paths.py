from __future__ import annotations

import json

from material_review_agent.models import (
    AgentDecision,
    AgentAction,
    ConfirmationReason,
    DraftSaveStatus,
    DraftStatus,
    RunStatus,
)


def _run(runtime, asset_id: str, requested_at: str, **kwargs):
    return runtime.run(
        asset_id=asset_id,
        requested_at=requested_at,
        timezone_name="Asia/Shanghai",
        **kwargs,
    )


def test_normal_path_saves_ready_draft(runtime, database):
    report = _run(runtime, "M01", "2026-11-01T20:00:00+08:00")

    assert report.run_status == RunStatus.COMPLETED
    assert report.audit_status.value == "PASS"
    assert report.draft_save_status.value == "SAVED"
    assert report.draft_status.value == "READY"
    assert report.requires_confirmation is False
    assert report.publication_status.value == "NOT_PUBLISHED"
    assert report.proposed_at.hour == 20
    assert report.model_steps >= 4
    assert report.tool_steps >= 4
    assert database.connection.execute("SELECT COUNT(*) FROM drafts").fetchone()[0] == 1
    assert database.connection.execute("SELECT COUNT(*) FROM schedule_entries").fetchone()[0] == 1

    trace = json.loads(report.trace_path and __import__("pathlib").Path(report.trace_path).read_text(encoding="utf-8"))
    actions = [
        item["action"]
        for item in trace["events"]
        if item["type"] == "model_decision"
    ]
    assert actions == [
        "read_material",
        "check_audit_rules",
        "query_schedule_slots",
        "save_schedule_draft",
        "finish",
    ]


def test_conflict_path_saves_alternative_pending_draft(runtime):
    report = _run(runtime, "M01", "2026-11-01T18:00:00+08:00")

    assert report.run_status == RunStatus.COMPLETED
    assert (report.proposed_at.hour, report.proposed_at.minute) == (18, 30)
    assert report.requested_at.hour == 18
    assert report.occupancy_minutes == 30
    assert report.draft_status.value == "PENDING_CONFIRMATION"
    assert report.requires_confirmation is True
    assert report.confirmation_reasons == ["ALTERNATIVE_TIME"]
    assert report.publication_status.value == "NOT_PUBLISHED"


def test_material_incomplete_and_rule_needs_info_do_not_save(runtime, database):
    missing_material = _run(runtime, "M03", "2026-11-01T20:00:00+08:00")
    missing_rule_data = _run(runtime, "M06", "2026-11-01T20:00:00+08:00")

    assert missing_material.run_status == RunStatus.INCOMPLETE
    assert missing_material.draft_id is None
    assert missing_rule_data.run_status == RunStatus.INCOMPLETE
    assert missing_rule_data.audit_status.value == "NEEDS_INFO"
    assert missing_rule_data.draft_id is None
    count = database.connection.execute("SELECT COUNT(*) FROM drafts").fetchone()[0]
    assert count == 0


def test_material_not_found_does_not_save(runtime, database):
    report = _run(runtime, "MISSING", "2026-11-01T20:00:00+08:00")

    assert report.run_status == RunStatus.INCOMPLETE
    assert report.audit_status is None
    assert report.draft_id is None
    assert report.draft_save_status == DraftSaveStatus.NOT_ATTEMPTED
    assert database.connection.execute("SELECT COUNT(*) FROM drafts").fetchone()[0] == 0


def test_blocked_and_warning_paths(runtime):
    blocked = _run(runtime, "M04", "2026-11-01T20:00:00+08:00")
    warning = _run(runtime, "M05", "2026-11-01T20:00:00+08:00")

    assert blocked.run_status == RunStatus.INCOMPLETE
    assert blocked.audit_status.value == "BLOCKED"
    assert blocked.draft_id is None

    assert warning.run_status == RunStatus.COMPLETED
    assert warning.audit_status.value == "WARNING"
    assert warning.draft_status.value == "PENDING_CONFIRMATION"
    assert warning.confirmation_reasons == ["RULE_WARNING"]


def test_semantic_violation_blocks_draft(runtime, database):
    report = _run(runtime, "M07", "2026-11-01T20:00:00+08:00")

    assert report.run_status == RunStatus.INCOMPLETE
    assert report.audit_status.value == "BLOCKED"
    assert report.draft_id is None
    assert any("保本收益" in reason for reason in report.risk_reasons)
    count = database.connection.execute("SELECT COUNT(*) FROM drafts").fetchone()[0]
    assert count == 0


def test_uncertain_semantic_audit_requires_review_and_does_not_save(
    settings,
    database,
):
    from material_review_agent.agent_runtime import AgentRuntime, build_services
    from material_review_agent.model_adapter import MockModel
    from material_review_agent.models import (
        SemanticEvaluationBatch,
        SemanticOutcome,
        SemanticRuleJudgment,
    )

    class UncertainSemanticEvaluator:
        name = "uncertain-test-semantic"

        def evaluate(self, material, rules):
            return SemanticEvaluationBatch(
                judgments=[
                    SemanticRuleJudgment(
                        rule_id="R006",
                        outcome=SemanticOutcome.UNCERTAIN,
                        reason="整体表达存在歧义",
                    )
                ]
            )

    services = build_services(database, UncertainSemanticEvaluator())
    runtime = AgentRuntime(settings, services, MockModel())
    report = _run(runtime, "M08", "2026-11-01T20:00:00+08:00")

    assert report.run_status == RunStatus.INCOMPLETE
    assert report.audit_status.value == "NEEDS_REVIEW"
    assert report.draft_id is None
    assert database.connection.execute("SELECT COUNT(*) FROM drafts").fetchone()[0] == 0


def test_duplicate_request_reuses_single_draft(runtime, database):
    first = _run(runtime, "M01", "2026-11-01T20:00:00+08:00")
    second = _run(runtime, "M01", "2026-11-01T20:00:00+08:00")

    count = database.connection.execute("SELECT COUNT(*) FROM drafts").fetchone()[0]
    assert count == 1
    assert second.draft_id == first.draft_id
    assert second.draft_save_status.value == "EXISTING"
    assert second.run_status == RunStatus.COMPLETED


def test_save_failure_is_explicit_and_rolls_back(runtime, database):
    report = _run(
        runtime,
        "M01",
        "2026-11-01T20:00:00+08:00",
        simulate_save_failure=True,
    )

    assert report.run_status == RunStatus.INCOMPLETE
    assert report.draft_id is None
    assert report.draft_save_status == DraftSaveStatus.STORAGE_ERROR
    assert "保存失败" in (report.unfinished_reason or "")
    count = database.connection.execute("SELECT COUNT(*) FROM drafts").fetchone()[0]
    assert count == 0


def test_tool_budget_stops_with_incomplete(settings, services, database):
    settings.max_tool_calls = 1
    from material_review_agent.agent_runtime import AgentRuntime
    from material_review_agent.model_adapter import MockModel

    runtime = AgentRuntime(settings, services, MockModel())
    report = _run(runtime, "M01", "2026-11-01T20:00:00+08:00")

    assert report.run_status == RunStatus.INCOMPLETE
    assert report.tool_steps == 1
    assert "上限" in (report.unfinished_reason or "")
    count = database.connection.execute("SELECT COUNT(*) FROM drafts").fetchone()[0]
    assert count == 0


def test_repeated_action_and_observation_stop_as_no_progress(settings, services):
    from material_review_agent.agent_runtime import AgentRuntime

    class RepeatingReadModel:
        name = "repeating-mock"

        def decide(self, state, allowed_actions):
            return AgentDecision(
                action=AgentAction.READ_MATERIAL.value,
                arguments={"asset_id": state.request.asset_id},
                decision_basis="重复读取同一素材。",
            )

    runtime = AgentRuntime(settings, services, RepeatingReadModel())
    report = _run(runtime, "M01", "2026-11-01T20:00:00+08:00")

    assert report.run_status == RunStatus.INCOMPLETE
    assert report.tool_steps == 2
    assert "无进展" in (report.unfinished_reason or "")
