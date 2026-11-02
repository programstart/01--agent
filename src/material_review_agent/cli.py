from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .agent_runtime import AgentRuntime, build_services
from .config import PROJECT_ROOT, Settings
from .db import Database
from .model_adapter import build_model
from .models import RunStatus
from .semantic_rules import build_semantic_evaluator


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="素材审核与发布排期 Agent（仅保存草稿，不执行真实发布）"
    )
    parser.add_argument("--asset-id", required=True, help="素材 ID，例如 M01")
    parser.add_argument(
        "--requested-at",
        required=True,
        help="请求发布时间，RFC 3339，例如 2026-11-01T18:00:00+08:00",
    )
    parser.add_argument("--timezone", default="Asia/Shanghai", help="IANA 时区")
    parser.add_argument(
        "--search-until",
        default=None,
        help="替代时段查询截止时间，必须与请求时间位于同一当地日期",
    )
    parser.add_argument(
        "--occupancy-minutes",
        type=int,
        default=None,
        help="单个发布请求占用的分钟数，默认 30",
    )
    parser.add_argument(
        "--slot-step-minutes",
        type=int,
        default=None,
        help="冲突后向后查找替代时段的步频，默认 30",
    )
    parser.add_argument(
        "--model",
        choices=("mock", "live"),
        default=None,
        help="模型模式；默认读取 MODEL_MODE，未配置时为 mock",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=None,
        help="SQLite 数据库路径；默认 data/demo.db",
    )
    parser.add_argument(
        "--trace-dir",
        type=Path,
        default=None,
        help="Trace 输出目录；默认 examples/traces",
    )
    parser.add_argument("--output", type=Path, default=None, help="可选 JSON 结果输出路径")
    parser.add_argument(
        "--simulate-save-failure",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    parser.add_argument("--max-steps", type=int, default=None, help=argparse.SUPPRESS)
    parser.add_argument("--max-tool-calls", type=int, default=None, help=argparse.SUPPRESS)
    return parser


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = build_parser()
    args = parser.parse_args(argv)
    settings = Settings.from_env()
    settings.db_path = args.db or settings.db_path
    settings.trace_dir = args.trace_dir or settings.trace_dir
    if args.model:
        settings.model_mode = args.model
    if args.max_steps is not None:
        settings.max_steps = args.max_steps
    if args.max_tool_calls is not None:
        settings.max_tool_calls = args.max_tool_calls
    if args.occupancy_minutes is not None:
        settings.occupancy_minutes = args.occupancy_minutes
    if args.slot_step_minutes is not None:
        settings.slot_step_minutes = args.slot_step_minutes
    if settings.occupancy_minutes <= 0 or settings.slot_step_minutes <= 0:
        raise ValueError("占用时长和替代步频必须大于 0")
    settings.simulate_save_failure = args.simulate_save_failure

    database = Database(settings.db_path)
    try:
        database.initialize(seed=True)
        model = build_model(settings)
        semantic_evaluator = build_semantic_evaluator(settings)
        services = build_services(database, semantic_evaluator)
        runtime = AgentRuntime(settings, services, model)
        report = runtime.run(
            asset_id=args.asset_id,
            requested_at=args.requested_at,
            timezone_name=args.timezone,
            search_until=args.search_until,
            simulate_save_failure=args.simulate_save_failure,
        )
        payload = report.model_dump(mode="json")
        rendered = json.dumps(payload, ensure_ascii=False, indent=2)
        print(rendered)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(rendered + "\n", encoding="utf-8")
        return 1 if report.run_status == RunStatus.FAILED else 0
    except (ValueError, RuntimeError) as exc:
        error_payload = {
            "run_status": "FAILED",
            "error": {
                "code": "INVALID_REQUEST_OR_CONFIGURATION",
                "message": str(exc),
            },
        }
        print(json.dumps(error_payload, ensure_ascii=False, indent=2), file=sys.stderr)
        return 2
    finally:
        database.close()


if __name__ == "__main__":
    raise SystemExit(main())
