"""AI spend guard. Checked BEFORE every Claude call; exceeding a limit blocks AI jobs safely."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import AIUsageRecord, Application, utcnow
from app.services.settings_service import get_section


class BudgetExceeded(Exception):
    def __init__(self, reason: str, retry_after: timedelta):
        super().__init__(reason)
        self.reason = reason
        self.retry_after = retry_after


@dataclass
class UsageWindow:
    calls_today: int
    tokens_month: int
    cost_month: float
    cost_today: float


def _period_starts(now: datetime) -> tuple[datetime, datetime]:
    day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    month = day.replace(day=1)
    return day, month


def _next_month(now: datetime) -> datetime:
    first = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return (first + timedelta(days=32)).replace(day=1)


def usage_window(db: Session, application_id: int | None = None, now: datetime | None = None) -> UsageWindow:
    now = now or utcnow()
    day, month = _period_starts(now)
    tokens = AIUsageRecord.input_tokens + AIUsageRecord.output_tokens + AIUsageRecord.cache_creation_tokens + AIUsageRecord.cache_read_tokens

    def q(*extra):
        stmt = select(func.count(AIUsageRecord.id), func.coalesce(func.sum(tokens), 0),
                      func.coalesce(func.sum(AIUsageRecord.estimated_cost), 0.0)).where(*extra)
        if application_id is not None:
            stmt = stmt.where(AIUsageRecord.application_id == application_id)
        return db.execute(stmt).one()

    calls_today, _, cost_today = q(AIUsageRecord.created_at >= day, AIUsageRecord.purpose != "connectivity_test")
    _, tokens_month, cost_month = q(AIUsageRecord.created_at >= month)
    return UsageWindow(int(calls_today), int(tokens_month), float(cost_month), float(cost_today))


def job_tokens(db: Session, job_id: int | None) -> int:
    if not job_id:
        return 0
    tokens = AIUsageRecord.input_tokens + AIUsageRecord.output_tokens + AIUsageRecord.cache_creation_tokens
    return int(db.scalar(select(func.coalesce(func.sum(tokens), 0)).where(AIUsageRecord.job_id == job_id)) or 0)


def check_budget(db: Session, application_id: int | None, job_id: int | None, est_tokens: int,
                 now: datetime | None = None) -> None:
    now = now or utcnow()
    cfg = get_section(db, "ai")
    g = cfg["budgets"]
    tomorrow = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    to_tomorrow = tomorrow - now + timedelta(minutes=1)
    to_month = _next_month(now) - now + timedelta(minutes=1)
    w = usage_window(db, None, now)
    if g.get("daily_calls") is not None and w.calls_today >= int(g["daily_calls"]):
        raise BudgetExceeded(f"Global daily Claude call limit reached ({w.calls_today}/{g['daily_calls']})", to_tomorrow)
    if g.get("monthly_tokens") is not None and w.tokens_month + est_tokens > int(g["monthly_tokens"]):
        raise BudgetExceeded(f"Global monthly token budget would be exceeded ({w.tokens_month:,} used of {int(g['monthly_tokens']):,})", to_month)
    if g.get("monthly_cost_usd") is not None and w.cost_month >= float(g["monthly_cost_usd"]):
        raise BudgetExceeded(f"Global monthly spend limit reached (${w.cost_month:.2f} of ${float(g['monthly_cost_usd']):.2f})", to_month)
    if job_id and g.get("max_tokens_per_job") and job_tokens(db, job_id) + est_tokens > int(g["max_tokens_per_job"]):
        raise BudgetExceeded(f"Per-job token limit ({g['max_tokens_per_job']:,}) would be exceeded", timedelta(hours=24))
    if application_id:
        app = db.get(Application, application_id)
        a = (app.ai_budget or {}) if app else {}
        if a:
            aw = usage_window(db, application_id, now)
            if a.get("daily_calls") not in (None, "") and aw.calls_today >= int(a["daily_calls"]):
                raise BudgetExceeded(f"{app.name}: daily Claude call limit reached ({aw.calls_today}/{a['daily_calls']})", to_tomorrow)
            if a.get("monthly_tokens") not in (None, "") and aw.tokens_month + est_tokens > int(a["monthly_tokens"]):
                raise BudgetExceeded(f"{app.name}: monthly token budget would be exceeded", to_month)
            if a.get("monthly_cost_usd") not in (None, "") and aw.cost_month >= float(a["monthly_cost_usd"]):
                raise BudgetExceeded(f"{app.name}: monthly spend limit reached (${aw.cost_month:.2f})", to_month)


def estimate_cost(pricing: dict, model: str, in_tok: int, out_tok: int, cache_read: int, cache_write: int) -> float:
    p_in, p_out, p_cr = pricing.get(model) or pricing.get("claude-opus-5-5") or [4.0, 20.0, 0.2]
    return round((in_tok * p_in + out_tok * p_out + cache_read * p_cr + cache_write * p_in * 1.25) / 1_000_000, 6)


def budget_status(db: Session) -> dict:
    cfg = get_section(db, "ai")
    w = usage_window(db)
    g = cfg["budgets"]
    reasons = []
    try:
        check_budget(db, None, None, 0)
    except BudgetExceeded as exc:
        reasons.append(exc.reason)
    return {"calls_today": w.calls_today, "tokens_month": w.tokens_month, "cost_month": round(w.cost_month, 4),
            "cost_today": round(w.cost_today, 4), "limits": g, "exceeded": bool(reasons), "reasons": reasons,
            "cost_saving_mode": cfg.get("cost_saving_mode", False)}
