from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from .db import Database
from .models import (
    AuditRuleRecord,
    AuditStatus,
    ConfirmationReason,
    DraftSaveStatus,
    DraftStatus,
    DraftView,
    MaterialRecord,
    OccupancyKind,
    PublicationStatus,
    RuleKind,
    RuleOperator,
    RuleSeverity,
    ScheduleEntryStatus,
    ScheduleRecordType,
    StructuredRuleCondition,
)


class RepositoryDataError(RuntimeError):
    pass


class DraftSlotConflictError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class OccupancyRow:
    record_type: ScheduleRecordType
    record_id: str
    asset_id: str
    publish_at: datetime
    publish_at_utc: datetime
    duration_minutes: int
    status: str
    occupancy: OccupancyKind
    idempotency_key: str | None = None


def parse_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def dump_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


class MaterialRepository:
    def __init__(self, database: Database):
        self.database = database

    def get(self, asset_id: str) -> MaterialRecord | None:
        row = self.database.connection.execute(
            """
            SELECT asset_id, title, platform, source_status, content,
                   metadata_json, version, updated_at
            FROM materials
            WHERE asset_id = ? COLLATE NOCASE
            """,
            (asset_id.strip(),),
        ).fetchone()
        if row is None:
            return None

        try:
            metadata = json.loads(row["metadata_json"])
        except (TypeError, json.JSONDecodeError) as exc:
            raise RepositoryDataError("素材元数据不是合法 JSON") from exc
        if not isinstance(metadata, dict):
            raise RepositoryDataError("素材元数据必须是 JSON 对象")

        return MaterialRecord(
            asset_id=row["asset_id"],
            title=row["title"],
            platform=row["platform"],
            source_status=row["source_status"],
            content=row["content"],
            metadata=metadata,
            version=row["version"],
            updated_at=parse_datetime(row["updated_at"]) if row["updated_at"] else None,
        )

    def snapshot_signature(self, asset_id: str) -> str | None:
        row = self.database.connection.execute(
            """
            SELECT asset_id, title, platform, source_status, content,
                   metadata_json, version, updated_at
            FROM materials
            WHERE asset_id = ? COLLATE NOCASE
            """,
            (asset_id.strip(),),
        ).fetchone()
        if row is None:
            return None
        return "|".join("" if value is None else str(value) for value in tuple(row))


class RuleRepository:
    def __init__(self, database: Database):
        self.database = database

    def list_enabled(self) -> list[AuditRuleRecord]:
        rows = self.database.connection.execute(
            """
            SELECT rule_id, rule_name, enabled, rule_kind, severity,
                   applies_to_json, policy_text, reason, priority, rule_version,
                   effective_from, effective_to
            FROM audit_rules
            WHERE enabled = 1
            ORDER BY priority ASC, rule_id ASC
            """
        ).fetchall()

        condition_rows = self.database.connection.execute(
            """
            SELECT rule_id, clause_index, field_path, operator, expected_value_json
            FROM audit_rule_conditions
            ORDER BY rule_id ASC, clause_index ASC
            """
        ).fetchall()
        conditions_by_rule: dict[str, list[StructuredRuleCondition]] = {}
        for row in condition_rows:
            try:
                expected_value = (
                    json.loads(row["expected_value_json"])
                    if row["expected_value_json"] is not None
                    else None
                )
                condition = StructuredRuleCondition(
                    clause_index=row["clause_index"],
                    field_path=row["field_path"],
                    operator=RuleOperator(row["operator"]),
                    expected_value=expected_value,
                )
            except (json.JSONDecodeError, ValueError) as exc:
                raise RepositoryDataError(f"规则 {row['rule_id']} 的检查条件损坏") from exc
            conditions_by_rule.setdefault(row["rule_id"], []).append(condition)

        records: list[AuditRuleRecord] = []
        for row in rows:
            try:
                applies_to = json.loads(row["applies_to_json"]) if row["applies_to_json"] else None
            except json.JSONDecodeError as exc:
                raise RepositoryDataError(f"规则 {row['rule_id']} 的 JSON 配置损坏") from exc
            if applies_to is not None and not isinstance(applies_to, list):
                raise RepositoryDataError(f"规则 {row['rule_id']} 的 applies_to 必须是数组")

            records.append(
                AuditRuleRecord(
                    rule_id=row["rule_id"],
                    rule_name=row["rule_name"],
                    enabled=bool(row["enabled"]),
                    rule_kind=RuleKind(row["rule_kind"]),
                    severity=RuleSeverity(row["severity"]),
                    applies_to=applies_to,
                    conditions=conditions_by_rule.get(row["rule_id"], []),
                    policy_text=row["policy_text"],
                    reason=row["reason"],
                    priority=row["priority"],
                    rule_version=row["rule_version"],
                    effective_from=parse_datetime(row["effective_from"]) if row["effective_from"] else None,
                    effective_to=parse_datetime(row["effective_to"]) if row["effective_to"] else None,
                )
            )
        return records


def _row_to_occupancy(row: sqlite3.Row, record_type: ScheduleRecordType) -> OccupancyRow:
    if record_type == ScheduleRecordType.SCHEDULE:
        return OccupancyRow(
            record_type=record_type,
            record_id=row["schedule_id"],
            asset_id=row["asset_id"],
            publish_at=parse_datetime(row["publish_at_local"]),
            publish_at_utc=parse_datetime(row["publish_at_utc"]),
            duration_minutes=row["duration_minutes"],
            status=row["status"],
            occupancy=OccupancyKind.HARD,
        )
    return OccupancyRow(
        record_type=record_type,
        record_id=row["draft_id"],
        asset_id=row["asset_id"],
        publish_at=parse_datetime(row["proposed_at_local"]),
        publish_at_utc=parse_datetime(row["proposed_at_utc"]),
        duration_minutes=row["duration_minutes"],
        status=row["draft_status"],
        occupancy=OccupancyKind.SOFT,
        idempotency_key=row["idempotency_key"],
    )


class ScheduleRepository:
    def __init__(self, database: Database):
        self.database = database

    def list_active_between(
        self,
        scope_id: str,
        start_utc: str,
        end_utc: str,
        exclude_idempotency_key: str | None = None,
        occupancy_minutes: int = 30,
    ) -> list[OccupancyRow]:
        window_end_utc = (
            parse_datetime(end_utc) + timedelta(minutes=occupancy_minutes)
        ).astimezone(UTC).isoformat().replace("+00:00", "Z")
        schedule_rows = self.database.connection.execute(
            """
            SELECT schedule_id, asset_id, publish_at_utc, publish_at_local,
                   duration_minutes, status
            FROM schedule_entries
            WHERE scope_id = ?
              AND status IN (?, ?)
              AND publish_at_utc < ?
              AND julianday(
                    publish_at_utc,
                    '+' || duration_minutes || ' minutes'
                  ) > julianday(?)
            ORDER BY publish_at_utc ASC
            """,
            (
                scope_id,
                ScheduleEntryStatus.CONFIRMED.value,
                ScheduleEntryStatus.PUBLISHED.value,
                window_end_utc,
                start_utc,
            ),
        ).fetchall()
        draft_rows = self.database.connection.execute(
            """
            SELECT draft_id, idempotency_key, asset_id, proposed_at_utc,
                   proposed_at_local, duration_minutes, draft_status
            FROM drafts
            WHERE scope_id = ?
              AND draft_status IN (?, ?)
              AND proposed_at_utc < ?
              AND julianday(
                    proposed_at_utc,
                    '+' || duration_minutes || ' minutes'
                  ) > julianday(?)
              AND (? IS NULL OR idempotency_key <> ?)
            ORDER BY proposed_at_utc ASC
            """,
            (
                scope_id,
                DraftStatus.READY.value,
                DraftStatus.PENDING_CONFIRMATION.value,
                window_end_utc,
                start_utc,
                exclude_idempotency_key,
                exclude_idempotency_key,
            ),
        ).fetchall()

        values = [_row_to_occupancy(row, ScheduleRecordType.SCHEDULE) for row in schedule_rows]
        values.extend(_row_to_occupancy(row, ScheduleRecordType.DRAFT) for row in draft_rows)
        values.sort(key=lambda item: (item.publish_at_utc, item.record_type.value, item.record_id))
        return values


class DraftRepository:
    def __init__(self, database: Database):
        self.database = database

    def find_by_idempotency(self, idempotency_key: str) -> DraftView | None:
        row = self.database.connection.execute(
            "SELECT * FROM drafts WHERE idempotency_key = ?",
            (idempotency_key,),
        ).fetchone()
        return self._row_to_draft(row) if row is not None else None

    def save(
        self,
        *,
        idempotency_key: str,
        asset_id: str,
        scope_id: str,
        requested_at: datetime,
        requested_at_utc: datetime,
        proposed_at: datetime,
        proposed_at_utc: datetime,
        duration_minutes: int,
        timezone: str,
        audit_status: AuditStatus,
        risk_rule_ids: list[str],
        risk_reasons: list[str],
        draft_status: DraftStatus,
        requires_confirmation: bool,
        confirmation_reasons: list[ConfirmationReason],
        fail_on_save: bool = False,
    ) -> tuple[DraftSaveStatus, DraftView]:
        connection = self.database.connection
        connection.execute("BEGIN IMMEDIATE")
        try:
            existing = self.find_by_idempotency(idempotency_key)
            if existing is not None:
                connection.execute("ROLLBACK")
                return DraftSaveStatus.EXISTING, existing

            proposed_start_utc = (
                proposed_at_utc.astimezone(UTC).isoformat().replace("+00:00", "Z")
            )
            proposed_end_utc = (
                proposed_at_utc.astimezone(UTC)
                + timedelta(minutes=duration_minutes)
            ).isoformat().replace("+00:00", "Z")
            schedule_conflict = connection.execute(
                """
                SELECT 1
                FROM schedule_entries
                WHERE scope_id = ?
                  AND status IN (?, ?)
                  AND julianday(publish_at_utc) < julianday(?)
                  AND julianday(
                        publish_at_utc,
                        '+' || duration_minutes || ' minutes'
                      ) > julianday(?)
                LIMIT 1
                """,
                (
                    scope_id,
                    ScheduleEntryStatus.CONFIRMED.value,
                    ScheduleEntryStatus.PUBLISHED.value,
                    proposed_end_utc,
                    proposed_start_utc,
                ),
            ).fetchone()
            draft_conflict = connection.execute(
                """
                SELECT 1
                FROM drafts
                WHERE scope_id = ?
                  AND draft_status IN (?, ?)
                  AND julianday(proposed_at_utc) < julianday(?)
                  AND julianday(
                        proposed_at_utc,
                        '+' || duration_minutes || ' minutes'
                      ) > julianday(?)
                  AND idempotency_key <> ?
                LIMIT 1
                """,
                (
                    scope_id,
                    DraftStatus.READY.value,
                    DraftStatus.PENDING_CONFIRMATION.value,
                    proposed_end_utc,
                    proposed_start_utc,
                    idempotency_key,
                ),
            ).fetchone()
            if schedule_conflict or draft_conflict:
                raise DraftSlotConflictError("目标时段已被其他有效排期占用")

            if fail_on_save:
                raise sqlite3.OperationalError("simulated draft storage failure")

            draft_id = f"D-{uuid.uuid4().hex[:12].upper()}"
            created_at = datetime.now(UTC).replace(microsecond=0)
            connection.execute(
                """
                INSERT INTO drafts (
                    draft_id, idempotency_key, asset_id, scope_id,
                    requested_at_utc, requested_at_local,
                    proposed_at_utc, proposed_at_local,
                    duration_minutes, timezone,
                    audit_status, risk_rule_ids_json, risk_reasons_json,
                    draft_status, requires_confirmation, confirmation_reasons_json,
                    publication_status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    draft_id,
                    idempotency_key,
                    asset_id,
                    scope_id,
                    requested_at_utc.astimezone(UTC).isoformat().replace("+00:00", "Z"),
                    requested_at.isoformat(),
                    proposed_at_utc.astimezone(UTC).isoformat().replace("+00:00", "Z"),
                    proposed_at.isoformat(),
                    duration_minutes,
                    timezone,
                    audit_status.value,
                    dump_json(risk_rule_ids),
                    dump_json(risk_reasons),
                    draft_status.value,
                    1 if requires_confirmation else 0,
                    dump_json([reason.value for reason in confirmation_reasons]),
                    PublicationStatus.NOT_PUBLISHED.value,
                    created_at.isoformat().replace("+00:00", "Z"),
                ),
            )
            row = connection.execute(
                "SELECT * FROM drafts WHERE draft_id = ?",
                (draft_id,),
            ).fetchone()
            connection.execute("COMMIT")
            return DraftSaveStatus.SAVED, self._row_to_draft(row)
        except Exception:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise

    @staticmethod
    def _row_to_draft(row: sqlite3.Row) -> DraftView:
        return DraftView(
            draft_id=row["draft_id"],
            asset_id=row["asset_id"],
            scope_id=row["scope_id"],
            requested_at=parse_datetime(row["requested_at_local"]),
            proposed_at=parse_datetime(row["proposed_at_local"]),
            duration_minutes=row["duration_minutes"],
            timezone=row["timezone"],
            audit_status=AuditStatus(row["audit_status"]),
            risk_rule_ids=json.loads(row["risk_rule_ids_json"]),
            risk_reasons=json.loads(row["risk_reasons_json"]),
            draft_status=DraftStatus(row["draft_status"]),
            requires_confirmation=bool(row["requires_confirmation"]),
            confirmation_reasons=[
                ConfirmationReason(value) for value in json.loads(row["confirmation_reasons_json"])
            ],
            publication_status=PublicationStatus(row["publication_status"]),
            created_at=parse_datetime(row["created_at"]),
        )
