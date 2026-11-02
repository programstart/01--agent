from __future__ import annotations

from datetime import UTC, datetime

from material_review_agent.models import (
    AuditCheckInput,
    AuditCheckStatus,
    AuditStatus,
    DraftSaveStatus,
    MaterialReadInput,
    MaterialReadStatus,
    RuleOutcome,
    SaveDraftContext,
    SaveDraftInput,
    ScheduleQueryContext,
    ScheduleQueryInput,
    ScheduleQueryStatus,
)
from material_review_agent.services import (
    DraftService,
    MaterialService,
    RuleEngine,
    ScheduleService,
)


def test_material_read_success_and_missing(database, services):
    service: MaterialService = services.materials
    success = service.read(MaterialReadInput(asset_id="m01"))
    assert success.status == MaterialReadStatus.SUCCESS
    assert success.material is not None
    assert success.material.asset_id == "M01"
    assert success.material.content_truncated is False

    missing = service.read(MaterialReadInput(asset_id="M03"))
    assert missing.status == MaterialReadStatus.INCOMPLETE
    assert "title" in missing.data_completeness.missing_fields


def test_material_not_found(services):
    result = services.materials.read(MaterialReadInput(asset_id="NOPE"))
    assert result.status == MaterialReadStatus.NOT_FOUND


def test_rule_check_pass_warning_block_and_missing_info(services):
    service: RuleEngine = services.rules
    requested_at = datetime.fromisoformat("2026-11-01T20:00:00+08:00")

    passed = service.check(
        AuditCheckInput(
            asset_id="M01",
            requested_at=requested_at,
            timezone="Asia/Shanghai",
        )
    )
    warning = service.check(
        AuditCheckInput(
            asset_id="M05",
            requested_at=requested_at,
            timezone="Asia/Shanghai",
        )
    )
    blocked = service.check(
        AuditCheckInput(
            asset_id="M04",
            requested_at=requested_at,
            timezone="Asia/Shanghai",
        )
    )
    needs_info = service.check(
        AuditCheckInput(
            asset_id="M06",
            requested_at=requested_at,
            timezone="Asia/Shanghai",
        )
    )

    assert passed.status == AuditCheckStatus.PASS
    assert warning.status == AuditCheckStatus.WARNING
    assert blocked.status == AuditCheckStatus.BLOCKED
    assert blocked.summary.blocking_violation_count == 1
    assert needs_info.status == AuditCheckStatus.NEEDS_INFO
    r002 = next(item for item in needs_info.rule_results if item.rule_id == "R002")
    assert r002.outcome == RuleOutcome.MISSING
    assert r002.missing_fields == ["material.metadata.rights_owner"]


def test_semantic_rule_catches_whole_content_without_keyword_match(services):
    service: RuleEngine = services.rules
    requested_at = datetime.fromisoformat("2026-11-01T20:00:00+08:00")
    result = service.check(
        AuditCheckInput(
            asset_id="M07",
            requested_at=requested_at,
            timezone="Asia/Shanghai",
        )
    )

    semantic = next(item for item in result.rule_results if item.rule_id == "R006")
    assert result.status == AuditCheckStatus.BLOCKED
    assert semantic.outcome == RuleOutcome.VIOLATED
    assert semantic.evidence_spans == ["无论市场怎么变化，你的本金都不会减少。"]


def test_semantic_rule_accepts_explicit_risk_statement(services):
    service: RuleEngine = services.rules
    requested_at = datetime.fromisoformat("2026-11-01T20:00:00+08:00")
    result = service.check(
        AuditCheckInput(
            asset_id="M08",
            requested_at=requested_at,
            timezone="Asia/Shanghai",
        )
    )

    semantic = next(item for item in result.rule_results if item.rule_id == "R006")
    assert result.status == AuditCheckStatus.PASS
    assert semantic.outcome == RuleOutcome.PASSED


def test_schedule_query_detects_conflict_and_returns_alternative(services):
    service: ScheduleService = services.schedules
    requested = datetime.fromisoformat("2026-11-01T18:00:00+08:00")
    result = service.query(
        ScheduleQueryInput(
            asset_id="M01",
            requested_at=requested,
            timezone="Asia/Shanghai",
            search_until=datetime.fromisoformat("2026-11-01T21:00:00+08:00"),
        ),
        ScheduleQueryContext(
            idempotency_key="test-key",
            scope_id="global",
            occupancy_minutes=30,
            slot_step_minutes=30,
            max_alternatives=3,
        ),
    )

    assert result.status == ScheduleQueryStatus.CONFLICT_WITH_ALTERNATIVE
    assert result.conflicts[0].record_id == "S001"
    assert result.conflicts[0].occupied_until.hour == 18
    assert result.conflicts[0].occupied_until.minute == 30
    assert [
        (item.publish_at.hour, item.publish_at.minute)
        for item in result.alternatives
    ] == [(18, 30), (19, 0), (19, 30)]


def test_schedule_query_available_and_no_slot(services):
    service: ScheduleService = services.schedules
    context = ScheduleQueryContext(idempotency_key="test-key")
    available = service.query(
        ScheduleQueryInput(
            asset_id="M01",
            requested_at=datetime.fromisoformat("2026-11-01T20:00:00+08:00"),
            timezone="Asia/Shanghai",
        ),
        context,
    )
    no_slot = service.query(
        ScheduleQueryInput(
            asset_id="M01",
            requested_at=datetime.fromisoformat("2026-11-01T18:00:00+08:00"),
            timezone="Asia/Shanghai",
            search_until=datetime.fromisoformat("2026-11-01T18:29:00+08:00"),
        ),
        context,
    )

    assert available.status == ScheduleQueryStatus.AVAILABLE
    assert no_slot.status == ScheduleQueryStatus.NO_FEASIBLE_SLOT


def test_schedule_interval_uses_adjacent_slot_and_30_minute_step(services):
    service: ScheduleService = services.schedules
    context = ScheduleQueryContext(idempotency_key="test-key")
    adjacent = service.query(
        ScheduleQueryInput(
            asset_id="M01",
            requested_at=datetime.fromisoformat("2026-11-01T18:30:00+08:00"),
            timezone="Asia/Shanghai",
        ),
        context,
    )
    shifted = service.query(
        ScheduleQueryInput(
            asset_id="M01",
            requested_at=datetime.fromisoformat("2026-11-01T18:15:00+08:00"),
            timezone="Asia/Shanghai",
            search_until=datetime.fromisoformat("2026-11-01T20:00:00+08:00"),
        ),
        context,
    )

    assert adjacent.status == ScheduleQueryStatus.AVAILABLE
    assert shifted.status == ScheduleQueryStatus.CONFLICT_WITH_ALTERNATIVE
    assert [
        (item.publish_at.hour, item.publish_at.minute)
        for item in shifted.alternatives
    ] == [(18, 45), (19, 15), (19, 45)]


def test_draft_save_transaction_rejects_overlapping_interval(services):
    service: DraftService = services.drafts
    first = service.save(
        SaveDraftInput(
            asset_id="M01",
            proposed_at=datetime.fromisoformat("2026-11-01T20:15:00+08:00"),
            timezone="Asia/Shanghai",
        ),
        SaveDraftContext(
            run_id="RUN-1",
            idempotency_key="first-slot",
            requested_at=datetime.fromisoformat("2026-11-01T20:15:00+08:00"),
            duration_minutes=30,
            audit_status=AuditStatus.PASS,
            schedule_check_status=ScheduleQueryStatus.AVAILABLE,
        ),
    )
    second = service.save(
        SaveDraftInput(
            asset_id="M02",
            proposed_at=datetime.fromisoformat("2026-11-01T20:00:00+08:00"),
            timezone="Asia/Shanghai",
        ),
        SaveDraftContext(
            run_id="RUN-2",
            idempotency_key="second-slot",
            requested_at=datetime.fromisoformat("2026-11-01T20:00:00+08:00"),
            duration_minutes=30,
            audit_status=AuditStatus.PASS,
            schedule_check_status=ScheduleQueryStatus.AVAILABLE,
        ),
    )

    assert first.status == DraftSaveStatus.SAVED
    assert second.status == DraftSaveStatus.REJECTED
    assert second.error is not None
    assert second.error.code == "SLOT_ALREADY_OCCUPIED"
