from __future__ import annotations

import sqlite3

from material_review_agent.db import Database


def test_legacy_rule_schema_is_replaced_with_v2(tmp_path):
    path = tmp_path / "legacy.db"
    connection = sqlite3.connect(path)
    connection.execute(
        """
        CREATE TABLE audit_rules (
            rule_id TEXT PRIMARY KEY,
            rule_name TEXT NOT NULL,
            enabled INTEGER NOT NULL,
            severity TEXT NOT NULL,
            applies_to_json TEXT,
            field_path TEXT NOT NULL,
            operator TEXT NOT NULL,
            expected_value_json TEXT,
            reason TEXT NOT NULL,
            priority INTEGER NOT NULL,
            effective_from TEXT,
            effective_to TEXT
        )
        """
    )
    connection.commit()
    connection.close()

    database = Database(path)
    try:
        database.initialize(seed=True)
        columns = {
            row["name"]
            for row in database.connection.execute("PRAGMA table_info(audit_rules)").fetchall()
        }
        assert "rule_kind" in columns
        assert "field_path" not in columns
        assert database.connection.execute(
            "SELECT rule_kind FROM audit_rules WHERE rule_id = 'R006'"
        ).fetchone()["rule_kind"] == "SEMANTIC"
    finally:
        database.close()


def test_point_schedule_schema_gains_30_minute_duration(tmp_path):
    path = tmp_path / "legacy-schedule.db"
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE schedule_entries (
            schedule_id TEXT PRIMARY KEY,
            asset_id TEXT NOT NULL,
            scope_id TEXT NOT NULL DEFAULT 'global',
            publish_at_utc TEXT NOT NULL,
            publish_at_local TEXT NOT NULL,
            timezone TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE drafts (
            draft_id TEXT PRIMARY KEY,
            idempotency_key TEXT NOT NULL UNIQUE,
            asset_id TEXT NOT NULL,
            scope_id TEXT NOT NULL DEFAULT 'global',
            requested_at_utc TEXT NOT NULL,
            requested_at_local TEXT NOT NULL,
            proposed_at_utc TEXT NOT NULL,
            proposed_at_local TEXT NOT NULL,
            timezone TEXT NOT NULL,
            audit_status TEXT NOT NULL,
            risk_rule_ids_json TEXT NOT NULL DEFAULT '[]',
            risk_reasons_json TEXT NOT NULL DEFAULT '[]',
            draft_status TEXT NOT NULL,
            requires_confirmation INTEGER NOT NULL,
            confirmation_reasons_json TEXT NOT NULL DEFAULT '[]',
            publication_status TEXT NOT NULL DEFAULT 'NOT_PUBLISHED',
            created_at TEXT NOT NULL
        );
        INSERT INTO schedule_entries VALUES (
            'S999', 'M00', 'global',
            '2026-11-01T10:00:00Z', '2026-11-01T18:00:00+08:00',
            'Asia/Shanghai', 'CONFIRMED', '2026-10-30T12:00:00Z'
        );
        """
    )
    connection.commit()
    connection.close()

    database = Database(path)
    try:
        database.initialize(seed=True)
        for table_name in ("schedule_entries", "drafts"):
            columns = {
                row["name"]
                for row in database.connection.execute(
                    f"PRAGMA table_info({table_name})"
                ).fetchall()
            }
            assert "duration_minutes" in columns
        assert database.connection.execute(
            "SELECT duration_minutes FROM schedule_entries WHERE schedule_id = 'S999'"
        ).fetchone()["duration_minutes"] == 30
        assert database.connection.execute("PRAGMA user_version").fetchone()[0] == 3
    finally:
        database.close()
