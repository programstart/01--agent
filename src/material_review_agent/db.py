from __future__ import annotations

import json
import sqlite3
from pathlib import Path


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS materials (
    asset_id TEXT PRIMARY KEY,
    title TEXT,
    platform TEXT,
    source_status TEXT NOT NULL,
    content TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    version INTEGER,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS audit_rules (
    rule_id TEXT PRIMARY KEY,
    rule_name TEXT NOT NULL,
    enabled INTEGER NOT NULL CHECK (enabled IN (0, 1)),
    rule_kind TEXT NOT NULL CHECK (rule_kind IN ('STRUCTURED', 'SEMANTIC')),
    severity TEXT NOT NULL CHECK (severity IN ('WARNING', 'BLOCKING')),
    applies_to_json TEXT,
    policy_text TEXT,
    reason TEXT NOT NULL,
    priority INTEGER NOT NULL,
    rule_version INTEGER NOT NULL DEFAULT 1,
    effective_from TEXT,
    effective_to TEXT
);

CREATE TABLE IF NOT EXISTS audit_rule_conditions (
    rule_id TEXT NOT NULL,
    clause_index INTEGER NOT NULL,
    field_path TEXT NOT NULL,
    operator TEXT NOT NULL,
    expected_value_json TEXT,
    PRIMARY KEY (rule_id, clause_index),
    FOREIGN KEY (rule_id) REFERENCES audit_rules(rule_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS schedule_entries (
    schedule_id TEXT PRIMARY KEY,
    asset_id TEXT NOT NULL,
    scope_id TEXT NOT NULL DEFAULT 'global',
    publish_at_utc TEXT NOT NULL,
    publish_at_local TEXT NOT NULL,
    duration_minutes INTEGER NOT NULL DEFAULT 30 CHECK (duration_minutes > 0),
    timezone TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('CONFIRMED', 'PUBLISHED', 'CANCELLED')),
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_schedule_scope_time
ON schedule_entries(scope_id, publish_at_utc);

CREATE TABLE IF NOT EXISTS drafts (
    draft_id TEXT PRIMARY KEY,
    idempotency_key TEXT NOT NULL UNIQUE,
    asset_id TEXT NOT NULL,
    scope_id TEXT NOT NULL DEFAULT 'global',
    requested_at_utc TEXT NOT NULL,
    requested_at_local TEXT NOT NULL,
    proposed_at_utc TEXT NOT NULL,
    proposed_at_local TEXT NOT NULL,
    duration_minutes INTEGER NOT NULL DEFAULT 30 CHECK (duration_minutes > 0),
    timezone TEXT NOT NULL,
    audit_status TEXT NOT NULL CHECK (audit_status IN ('PASS', 'WARNING')),
    risk_rule_ids_json TEXT NOT NULL DEFAULT '[]',
    risk_reasons_json TEXT NOT NULL DEFAULT '[]',
    draft_status TEXT NOT NULL CHECK (draft_status IN ('READY', 'PENDING_CONFIRMATION')),
    requires_confirmation INTEGER NOT NULL CHECK (requires_confirmation IN (0, 1)),
    confirmation_reasons_json TEXT NOT NULL DEFAULT '[]',
    publication_status TEXT NOT NULL DEFAULT 'NOT_PUBLISHED',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_draft_scope_time
ON drafts(scope_id, proposed_at_utc);
"""


class Database:
    def __init__(self, path: str | Path):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.path = str(path)
        self.connection = sqlite3.connect(self.path, isolation_level=None)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.execute("PRAGMA journal_mode = WAL")

    def initialize(self, seed: bool = True) -> None:
        self._replace_legacy_rule_schema()
        self.connection.executescript(SCHEMA)
        self._migrate_schedule_duration_schema()
        self.connection.execute("PRAGMA user_version = 3")
        if seed:
            self.seed_demo_data()

    def _replace_legacy_rule_schema(self) -> None:
        row = self.connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'audit_rules'"
        ).fetchone()
        if row is None:
            return
        columns = {
            item["name"]
            for item in self.connection.execute("PRAGMA table_info(audit_rules)").fetchall()
        }
        if "rule_kind" in columns:
            return
        if self.connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'audit_rule_conditions'"
        ).fetchone():
            self.connection.execute("DROP TABLE audit_rule_conditions")
        self.connection.execute("DROP TABLE audit_rules")

    def _migrate_schedule_duration_schema(self) -> None:
        for table_name in ("schedule_entries", "drafts"):
            table = self.connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?",
                (table_name,),
            ).fetchone()
            if table is None:
                continue
            columns = {
                item["name"]
                for item in self.connection.execute(
                    f"PRAGMA table_info({table_name})"
                ).fetchall()
            }
            if "duration_minutes" not in columns:
                self.connection.execute(
                    f"""
                    ALTER TABLE {table_name}
                    ADD COLUMN duration_minutes INTEGER NOT NULL DEFAULT 30
                    CHECK (duration_minutes > 0)
                    """
                )

    def seed_demo_data(self) -> None:
        created_at = "2026-10-30T12:00:00Z"
        materials = [
            (
                "M00",
                "已确认排期素材",
                "short_video",
                "approved",
                "已确认排期占位素材",
                json.dumps({"rights_owner": "rights-team", "claim_review_required": False}, ensure_ascii=False),
                1,
                created_at,
            ),
            (
                "M01",
                "季度活动宣传视频",
                "short_video",
                "pending_review",
                "面向新用户的季度活动宣传内容。",
                json.dumps({"rights_owner": "rights-alpha", "claim_review_required": False}, ensure_ascii=False),
                1,
                created_at,
            ),
            (
                "M02",
                "产品功能说明",
                "short_video",
                "pending_review",
                "介绍产品功能和具体操作方式。",
                json.dumps({"rights_owner": "rights-beta", "claim_review_required": False}, ensure_ascii=False),
                1,
                created_at,
            ),
            (
                "M03",
                None,
                "short_video",
                "pending_review",
                "标题缺失的测试素材。",
                json.dumps({"rights_owner": "rights-gamma", "claim_review_required": False}, ensure_ascii=False),
                1,
                created_at,
            ),
            (
                "M04",
                "违规宣传测试",
                "short_video",
                "pending_review",
                "本产品保证收益，适合所有用户。",
                json.dumps({"rights_owner": "rights-delta", "claim_review_required": False}, ensure_ascii=False),
                1,
                created_at,
            ),
            (
                "M05",
                "风险提示测试",
                "short_video",
                "pending_review",
                "需要人工确认宣传表达的活动内容。",
                json.dumps({"rights_owner": "rights-epsilon", "claim_review_required": True}, ensure_ascii=False),
                1,
                created_at,
            ),
            (
                "M06",
                "资料不足规则测试",
                "short_video",
                "pending_review",
                "素材基础字段完整，但缺少权利归属信息。",
                json.dumps({"claim_review_required": False}, ensure_ascii=False),
                1,
                created_at,
            ),
            (
                "M07",
                "整体语义风险测试",
                "short_video",
                "pending_review",
                "无论市场怎么变化，你的本金都不会减少。",
                json.dumps({"rights_owner": "rights-zeta", "claim_review_required": False}, ensure_ascii=False),
                1,
                created_at,
            ),
            (
                "M08",
                "语义合规表达测试",
                "short_video",
                "pending_review",
                "本产品存在市场波动风险，不承诺本金不受损失。",
                json.dumps({"rights_owner": "rights-eta", "claim_review_required": False}, ensure_ascii=False),
                1,
                created_at,
            ),
        ]
        self.connection.executemany(
            """
            INSERT OR IGNORE INTO materials
            (asset_id, title, platform, source_status, content, metadata_json, version, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            materials,
        )

        rules = [
            (
                "R001",
                "标题必填",
                1,
                "STRUCTURED",
                "BLOCKING",
                "[]",
                None,
                "素材标题缺失",
                10,
                1,
                None,
                None,
            ),
            (
                "R002",
                "权利归属必填",
                1,
                "STRUCTURED",
                "BLOCKING",
                "[]",
                None,
                "素材缺少权利归属信息",
                20,
                1,
                None,
                None,
            ),
            (
                "R003",
                "来源状态允许",
                1,
                "STRUCTURED",
                "BLOCKING",
                "[]",
                None,
                "素材来源状态不允许进入发布流程",
                30,
                1,
                None,
                None,
            ),
            (
                "R004",
                "禁止产品功效承诺",
                1,
                "STRUCTURED",
                "BLOCKING",
                "[]",
                None,
                "素材包含禁止使用的产品功效承诺",
                40,
                1,
                None,
                None,
            ),
            (
                "R005",
                "宣传表达需要确认",
                1,
                "STRUCTURED",
                "WARNING",
                "[]",
                None,
                "素材包含需要人工确认的宣传表达",
                50,
                1,
                None,
                None,
            ),
            (
                "R006",
                "禁止整体暗示保本收益",
                1,
                "SEMANTIC",
                "BLOCKING",
                "[]",
                "素材不得直接或间接暗示本金不会减少、收益有保证或不存在风险。",
                "素材整体表达暗示保本收益或收益保证",
                60,
                1,
                None,
                None,
            ),
        ]
        self.connection.executemany(
            """
            INSERT OR IGNORE INTO audit_rules
            (rule_id, rule_name, enabled, rule_kind, severity, applies_to_json,
             policy_text, reason, priority, rule_version, effective_from, effective_to)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rules,
        )

        conditions = [
            ("R001", 0, "material.title", "REQUIRED", "null"),
            ("R002", 0, "material.metadata.rights_owner", "REQUIRED", "null"),
            (
                "R003",
                0,
                "material.source_status",
                "IN",
                json.dumps(["pending_review", "approved"], ensure_ascii=False),
            ),
            (
                "R004",
                0,
                "material.content",
                "CONTAINS_ANY",
                json.dumps(["保证收益", "稳赚不赔", "治愈"], ensure_ascii=False),
            ),
            (
                "R005",
                0,
                "material.metadata.claim_review_required",
                "EQUALS",
                "false",
            ),
        ]
        self.connection.executemany(
            """
            INSERT OR IGNORE INTO audit_rule_conditions
            (rule_id, clause_index, field_path, operator, expected_value_json)
            VALUES (?, ?, ?, ?, ?)
            """,
            conditions,
        )

        self.connection.execute(
            """
            INSERT OR IGNORE INTO schedule_entries
            (schedule_id, asset_id, scope_id, publish_at_utc, publish_at_local,
             duration_minutes, timezone, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "S001",
                "M00",
                "global",
                "2026-11-01T10:00:00Z",
                "2026-11-01T18:00:00+08:00",
                30,
                "Asia/Shanghai",
                "CONFIRMED",
                created_at,
            ),
        )

    def close(self) -> None:
        self.connection.close()
