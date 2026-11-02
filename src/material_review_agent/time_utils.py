from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def get_timezone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"无效的 IANA 时区: {name}") from exc


def normalize_datetime(value: str | datetime, timezone_name: str) -> tuple[datetime, datetime]:
    zone = get_timezone(timezone_name)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError as exc:
            raise ValueError("发布时间必须符合 RFC 3339 格式") from exc
    else:
        parsed = value

    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("发布时间必须包含时区偏移")
    if parsed.second != 0 or parsed.microsecond != 0:
        raise ValueError("第一阶段发布时间必须为分钟精度，秒和微秒必须为 00")

    local_value = parsed.astimezone(zone)
    utc_value = parsed.astimezone(UTC)
    return local_value.replace(second=0, microsecond=0), utc_value.replace(second=0, microsecond=0)


def utc_key(value: datetime) -> str:
    return value.astimezone(UTC).replace(second=0, microsecond=0).isoformat().replace("+00:00", "Z")


def local_iso(value: datetime, timezone_name: str) -> str:
    return value.astimezone(get_timezone(timezone_name)).replace(second=0, microsecond=0).isoformat()


def default_search_until(requested_local: datetime) -> datetime:
    return datetime.combine(
        requested_local.date(),
        time(hour=23, minute=59),
        tzinfo=requested_local.tzinfo,
    )


def validate_search_window(
    requested_local: datetime,
    search_until: datetime | None,
    timezone_name: str,
) -> datetime:
    zone = get_timezone(timezone_name)
    boundary = default_search_until(requested_local) if search_until is None else search_until
    if boundary.tzinfo is None or boundary.utcoffset() is None:
        raise ValueError("search_until 必须包含时区偏移")
    if boundary.second != 0 or boundary.microsecond != 0:
        raise ValueError("search_until 必须为分钟精度")
    boundary_local = boundary.astimezone(zone)
    if boundary_local.date() != requested_local.date():
        raise ValueError("第一阶段 search_until 必须与请求时间位于同一当地日期")
    if boundary_local < requested_local:
        raise ValueError("search_until 不得早于发布时间")
    return boundary_local


def generate_grid_candidates(
    requested_local: datetime,
    search_until_local: datetime,
    step_minutes: int,
    max_search: int = 48,
) -> list[datetime]:
    if step_minutes <= 0:
        raise ValueError("时间网格步长必须大于 0")

    candidates: list[datetime] = []
    current = requested_local + timedelta(minutes=step_minutes)
    while current <= search_until_local and len(candidates) < max_search:
        candidates.append(current)
        current += timedelta(minutes=step_minutes)
    return candidates
