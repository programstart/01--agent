from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class TraceRecorder:
    def __init__(self, run_id: str, trace_dir: str | Path):
        self.run_id = run_id
        self.trace_dir = Path(trace_dir)
        self.events: list[dict[str, Any]] = []

    def record(self, event_type: str, **fields: Any) -> None:
        event = {
            "type": event_type,
            "timestamp": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            **fields,
        }
        self.events.append(event)

    def write(self) -> Path:
        self.trace_dir.mkdir(parents=True, exist_ok=True)
        path = self.trace_dir / f"{self.run_id}.json"
        payload = {
            "run_id": self.run_id,
            "events": self.events,
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return path
