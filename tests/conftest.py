from __future__ import annotations

from pathlib import Path

import pytest

from material_review_agent.agent_runtime import AgentRuntime, build_services
from material_review_agent.config import Settings
from material_review_agent.db import Database
from material_review_agent.model_adapter import MockModel
from material_review_agent.semantic_rules import MockSemanticRuleEvaluator


@pytest.fixture
def database(tmp_path: Path) -> Database:
    db = Database(tmp_path / "test.db")
    db.initialize(seed=True)
    yield db
    db.close()


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        db_path=tmp_path / "test.db",
        trace_dir=tmp_path / "traces",
        model_mode="mock",
        max_steps=8,
        max_tool_calls=6,
        occupancy_minutes=30,
        slot_step_minutes=30,
        max_alternatives=3,
    )


@pytest.fixture
def services(database: Database):
    return build_services(database, MockSemanticRuleEvaluator())


@pytest.fixture
def runtime(settings: Settings, services):
    return AgentRuntime(settings, services, MockModel())
