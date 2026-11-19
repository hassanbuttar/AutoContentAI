"""Pure slot computation for publishing rules (fully unit-tested, no DB access).

All returned datetimes are naive UTC. Local times are interpreted in the rule's timezone,
DST-aware via zoneinfo.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo


@dataclass
class RuleSpec:
    frequency: str = "daily"  # daily|weekly
    posts_per_period: int = 1
    days_of_week: list[int] = field(default_factory=lambda: list(range(7)))  # Mon=0
    posting_times: list[str] = field(default_factory=list)
    window_start: str = "09:00"
    window_end: str = "21:00"
    timezone: str = "UTC"
    start_date: str | None = None
    end_date: str | None = None
    blackout_periods: list[dict] = field(default_factory=list)  # [{"start": "YYYY-MM-DDTHH:MM", "end": ...}] local
    max_per_day: int | None = None

    @classmethod
    def from_model(cls, r) -> "RuleSpec":
        return cls(frequency=r.frequency, posts_per_period=r.posts_per_period, days_of_week=list(r.days_of_week or []),
                   posting_times=list(r.posting_times or []), window_start=r.window_start, window_end=r.window_end,
                   timezone=r.timezone or "UTC", start_date=r.start_date, end_date=r.end_date,
                   blackout_periods=list(r.blackout_periods or []), max_per_day=r.max_per_day)


def _parse_hm(s: str) -> time:
    h, m = s.strip().split(":")[:2]
    return time(int(h), int(m))


def distribute_times(n: int, window_start: str, window_end: str) -> list[time]:
    """Evenly spread n posts across [start, end) using slot midpoints, rounded to 5 minutes."""
    if n <= 0:
        return []
    ws, we = _parse_hm(window_start), _parse_hm(window_end)
    start_min = ws.hour * 60 + ws.minute
    end_min = we.hour * 60 + we.minute
    if end_min <= start_min:
        end_min = start_min + 60
    span = end_min - start_min
    out: list[time] = []
    for i in range(n):
        m = start_min + (i + 0.5) * span / n
        m = int(round(m / 5.0) * 5)
        m = min(m, 23 * 60 + 55)
        out.append(time(m // 60, m % 60))
    # de-duplicate (very small windows)
    seen: list[time] = []
    for t in out:
        if t not in seen:
            seen.append(t)
    return seen


def times_for_day(spec: RuleSpec, count: int) -> list[time]:
    explicit = sorted({_parse_hm(t) for t in spec.posting_times if t})
    if explicit:
        if len(explicit) >= count:
            return explicit[:count]
        # more posts than explicit times: fill remaining evenly in the window
        extra = [t for t in distribute_times(count, spec.window_start, spec.window_end) if t not in explicit]
        return sorted(explicit + extra[: count - len(explicit)])
    return distribute_times(count, spec.window_start, spec.window_end)


def weekly_days(allowed: list[int], n: int) -> list[int]:
    """Pick n weekdays spread across the allowed days (e.g. 3 of Mon-Sun -> Mon, Wed, Fri-ish)."""
    allowed = sorted(set(d for d in allowed if 0 <= d <= 6)) or list(range(7))
    if n >= len(allowed):
        return allowed
    step = len(allowed) / n
    return sorted({allowed[int(i * step)] for i in range(n)})


def _in_blackout(local_dt: datetime, periods: list[dict]) -> bool:
    for p in periods:
        try:
            s = datetime.fromisoformat(p["start"])
            e = datetime.fromisoformat(p["end"])
        except (KeyError, ValueError, TypeError):
            continue
        if s.tzinfo:
            s = s.replace(tzinfo=None)
        if e.tzinfo:
            e = e.replace(tzinfo=None)
        if s <= local_dt.replace(tzinfo=None) < e:
            return True
    return False


def compute_slots(spec: RuleSpec, start_utc: datetime, end_utc: datetime) -> list[datetime]:
    """Slots (naive UTC) with start_utc <= slot < end_utc."""
    tz = ZoneInfo(spec.timezone or "UTC")
    s_local = start_utc.replace(tzinfo=timezone.utc).astimezone(tz)
    e_local = end_utc.replace(tzinfo=timezone.utc).astimezone(tz)
    first_day: date = s_local.date() - timedelta(days=1)
    last_day: date = e_local.date() + timedelta(days=1)
    camp_start = date.fromisoformat(spec.start_date) if spec.start_date else None
    camp_end = date.fromisoformat(spec.end_date) if spec.end_date else None
    allowed = sorted(set(spec.days_of_week)) if spec.days_of_week else list(range(7))
    n = max(0, int(spec.posts_per_period))
    out: list[datetime] = []
    day = first_day
    while day <= last_day:
        if (camp_start and day < camp_start) or (camp_end and day > camp_end):
            day += timedelta(days=1)
            continue
        if spec.frequency == "weekly":
            chosen = weekly_days(allowed, n) if n else []
            count = 1 if day.weekday() in chosen else 0
            if n > len(allowed) and day.weekday() in allowed:  # more posts than days: stack extra on early days
                base, extra = divmod(n, len(allowed))
                count = base + (1 if allowed.index(day.weekday()) < extra else 0)
        else:
            count = n if day.weekday() in allowed else 0
        if spec.max_per_day:
            count = min(count, spec.max_per_day)
        if count:
            for t in times_for_day(spec, count):
                local = datetime.combine(day, t).replace(tzinfo=tz)
                # normalise DST gaps: round-trip through UTC
                utc = local.astimezone(timezone.utc).replace(tzinfo=None)
                if _in_blackout(datetime.combine(day, t), spec.blackout_periods):
                    continue
                if start_utc <= utc < end_utc:
                    out.append(utc)
        day += timedelta(days=1)
    return sorted(set(out))
