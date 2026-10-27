"""Date context for the prompt, and local <-> UTC conversion.

The model only ever sees and writes local times. cal.com only ever sees UTC.
"""

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo


def now_local(tz: str) -> datetime:
    return datetime.now(ZoneInfo(tz))


def date_context(tz: str, now: datetime | None = None) -> str:
    now = now or now_local(tz)
    days = [now.date() + timedelta(days=i) for i in range(15)]
    lines = [f"{d:%a} {d.isoformat()}" + (" (today)" if i == 0 else "") for i, d in enumerate(days)]
    return (
        f"Now: {now:%A %Y-%m-%d %H:%M} in {tz}.\n"
        "Next 14 days:\n" + "\n".join(lines) + "\n"
        "Vague times: morning 09:00-12:00, afternoon 12:00-17:00, evening 17:00-20:00."
    )


def to_utc(local: str, tz: str) -> str:
    """'2026-10-29T14:00' in tz -> '2026-10-29T21:00:00Z'. An explicit offset wins over tz."""
    dt = datetime.fromisoformat(local)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo(tz))
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def to_local(utc: str, tz: str) -> datetime:
    return datetime.fromisoformat(utc.replace("Z", "+00:00")).astimezone(ZoneInfo(tz))


def day_range_utc(start: str, end: str, tz: str) -> tuple[str, str]:
    """Local dates 'YYYY-MM-DD' (inclusive) -> UTC bounds covering those whole days."""
    zone = ZoneInfo(tz)
    lo = datetime.combine(date.fromisoformat(start), time.min, zone)
    hi = datetime.combine(date.fromisoformat(end) + timedelta(days=1), time.min, zone)
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    return lo.astimezone(timezone.utc).strftime(fmt), hi.astimezone(timezone.utc).strftime(fmt)
