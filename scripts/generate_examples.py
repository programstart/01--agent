from __future__ import annotations

import io
import json
import os
import shutil
import sys
from contextlib import redirect_stdout
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from material_review_agent.cli import main  # noqa: E402


SCENARIOS = (
    "01-normal",
    "02-conflict",
    "03-missing-data",
    "04-rule-blocked",
    "05-save-failure",
)


def run_scenario(name: str) -> None:
    scenario_dir = PROJECT_ROOT / "examples" / name
    input_data = json.loads((scenario_dir / "input.json").read_text(encoding="utf-8"))
    trace_dir = scenario_dir / "traces"
    trace_dir.mkdir(parents=True, exist_ok=True)
    for old_trace in trace_dir.glob("RUN-*.json"):
        old_trace.unlink()

    db_path = PROJECT_ROOT / "data" / f"example-{name}.db"
    for suffix in ("", "-shm", "-wal"):
        candidate = Path(str(db_path) + suffix)
        if candidate.exists():
            candidate.unlink()

    trace_arg = Path("examples") / name / "traces"
    output_arg = Path("examples") / name / "output.json"
    db_arg = Path("data") / f"example-{name}.db"
    argv = [
        "--asset-id",
        input_data["asset_id"],
        "--requested-at",
        input_data["requested_at"],
        "--timezone",
        input_data["timezone"],
        "--model",
        input_data["model"],
        "--db",
        str(db_arg),
        "--trace-dir",
        str(trace_arg),
        "--output",
        str(output_arg),
    ]
    if input_data.get("simulate_save_failure"):
        argv.append("--simulate-save-failure")

    with redirect_stdout(io.StringIO()):
        exit_code = main(argv)
    if exit_code not in (0, 1):
        raise RuntimeError(f"{name} 生成失败，CLI 退出码 {exit_code}")

    output_data = json.loads((scenario_dir / "output.json").read_text(encoding="utf-8"))
    source_trace = PROJECT_ROOT / output_data["trace_path"]
    shutil.copyfile(source_trace, scenario_dir / "trace.json")

    for suffix in ("", "-shm", "-wal"):
        candidate = Path(str(db_path) + suffix)
        if candidate.exists():
            candidate.unlink()


def main_entry() -> int:
    os.chdir(PROJECT_ROOT)
    for scenario in SCENARIOS:
        run_scenario(scenario)
        print(f"generated examples/{scenario}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main_entry())
