from __future__ import annotations

import json

import pytest

from material_review_agent.cli import main
from material_review_agent.config import Settings
from material_review_agent.model_adapter import build_model


def test_cli_mock_path(tmp_path, capsys):
    exit_code = main(
        [
            "--asset-id",
            "M01",
            "--requested-at",
            "2026-11-01T20:00:00+08:00",
            "--timezone",
            "Asia/Shanghai",
            "--model",
            "mock",
            "--db",
            str(tmp_path / "cli.db"),
            "--trace-dir",
            str(tmp_path / "traces"),
        ]
    )
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert exit_code == 0
    assert payload["run_status"] == "COMPLETED"
    assert payload["draft_status"] == "READY"
    assert payload["publication_status"] == "NOT_PUBLISHED"


def test_live_model_requires_credentials(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    settings = Settings(model_mode="live", model_name="example-model")

    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        build_model(settings)
