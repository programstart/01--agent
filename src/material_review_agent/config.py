from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


@dataclass(slots=True)
class Settings:
    db_path: Path = field(default_factory=lambda: PROJECT_ROOT / "data" / "demo.db")
    trace_dir: Path = field(default_factory=lambda: PROJECT_ROOT / "examples" / "traces")
    model_mode: str = "mock"
    model_name: str | None = None
    model_base_url: str | None = None
    max_steps: int = 8
    max_tool_calls: int = 6
    max_read_retries: int = 1
    occupancy_minutes: int = 30
    slot_step_minutes: int = 30
    max_alternatives: int = 3
    workspace_id: str = "default"
    scope_id: str = "global"
    simulate_save_failure: bool = False

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            model_mode=os.getenv("MODEL_MODE", "mock").strip().lower(),
            model_name=os.getenv("OPENAI_MODEL") or None,
            model_base_url=os.getenv("OPENAI_BASE_URL") or None,
            occupancy_minutes=int(os.getenv("OCCUPANCY_MINUTES", "30")),
            slot_step_minutes=int(os.getenv("SLOT_STEP_MINUTES", "30")),
        )
