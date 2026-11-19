"""Content planning orchestrator: topic -> (Claude single structured call | local template) -> validated plan
-> claim check -> repetition check -> persisted idea/script/storyboard.

Claude is called at most 1 + max_retries times per plan, never in an open loop."""
from __future__ import annotations

import logging
import random
from dataclasses import dataclass, field
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (Application, ApplicationAsset, ContentIdea, ProjectChangeEvent, SocialAccount, Storyboard,
                        VideoScript, utcnow)
from app.services.ai import prompts
from app.services.ai.budget import BudgetExceeded
from app.services.ai.client import AIRequestError, AIUnavailable, ai_available, structured_call, system_with_profile
from app.services.ai.schemas import PLAN_SCHEMA, ContentPlan
from app.services.content import claims as claim_check
from app.services.content.dedup import max_script_similarity, recent_ideas
from app.services.content.local_planner import build_local_plan
from app.services.content.topics import TopicCandidate, _facts_for_text, select_topic
from app.services.knowledge.analyzer import compact_profile
from app.services.settings_service import effective_video_settings, get_section

log = logging.getLogger(__name__)


@dataclass
class PlanResult:
    idea: ContentIdea
    script: VideoScript
    storyboard: Storyboard
    used_ai: bool
    notes: list[str] = field(default_factory=list)


def _screenshots(db: Session, app_id: int) -> list[dict]:
    rows = db.scalars(select(ApplicationAsset).where(ApplicationAsset.application_id == app_id,
                                                     ApplicationAsset.kind == "screenshot").limit(20))
    return [{"id": a.id, "description": (a.description or a.original_name)[:160]} for a in rows]


def _platforms(db: Session, app_id: int) -> list[str]:
    ps = sorted({a.platform for a in db.scalars(select(SocialAccount).where(SocialAccount.application_id == app_id))})
    return [p for p in ps if p != "export"] or ["instagram", "tiktok", "youtube"]


def _sanitize_plan(plan: ContentPlan, screenshot_ids: set[int], comfy_enabled: bool) -> None:
    """AI output references assets by ID only; unknown IDs are downgraded, never treated as paths."""
    for s in plan.scenes:
        if s.visual_source == "application_screenshot" and s.screenshot_asset_id not in screenshot_ids:
            s.visual_source = "comfyui" if (comfy_enabled and s.visual_prompt) else "title_card"
            s.screenshot_asset_id = 0
        if s.visual_source == "comfyui" and not comfy_enabled:
            s.visual_source = "title_card"


def fit_duration(plan: ContentPlan, lo: float, hi: float) -> bool:
    """Scale scene durations so the total lands inside [lo, hi] (renderer may still extend for narration)."""
    total = sum(s.duration_seconds for s in plan.scenes)
    target = lo if total < lo else hi if total > hi else None
    if target is None or total <= 0:
        return False
    k = target / total
    for s in plan.scenes:
        s.duration_seconds = round(min(15.0, max(1.5, s.duration_seconds * k)), 2)
    plan.duration_seconds = round(sum(s.duration_seconds for s in plan.scenes), 2)
    return True


def plan_content(db: Session, app: Application, *, job_id: int | None = None, content_type: str | None = None,
                 topic: str | None = None, force_local: bool = False, log_fn=None) -> PlanResult:
    say = log_fn or (lambda m, lvl="info": None)
    if app.knowledge is None:
        from app.services.knowledge.analyzer import analyze_application

        say("No knowledge profile yet; analyzing application first")
        analyze_application(db, app, job_id=job_id, log_fn=say)
        db.refresh(app)
    k = app.knowledge
    facts = [f for f in (k.facts or []) if f.get("active", True)]
    ai_cfg = get_section(db, "ai")
    dd = get_section(db, "dedup")
    comfy = get_section(db, "comfyui")
    video = effective_video_settings(db, app.video_settings)
    comfy_enabled = bool(comfy.get("enabled") and comfy.get("use_for_scenes"))
    shots = _screenshots(db, app.id)
    notes: list[str] = []
    rng = random.Random()

    allowed_types = (app.content_strategy or {}).get("content_types") or None
    if topic:
        ctype = content_type or "feature_announcement"
        cand = TopicCandidate(ctype, topic[:300], _facts_for_text(facts, topic, 3), [], reason="manual topic")
    else:
        cand = select_topic(db, app, content_types=[content_type] if content_type else allowed_types,
                            cooldown_days=int(dd["topic_cooldown_days"]), rng=rng,
                            significance_threshold=float(ai_cfg["significance_threshold"]))
    say(f"Topic: [{cand.content_type}] {cand.topic} ({cand.reason})")

    ok, why = ai_available(db)
    use_ai = ok and not force_local and not ai_cfg.get("cost_saving_mode")
    if not use_ai:
        notes.append(f"local planner ({'cost-saving mode' if ai_cfg.get('cost_saving_mode') else ('forced' if force_local else why)})")
    plan: ContentPlan | None = None
    usage_id = None
    if use_ai:
        relevant = [f for f in facts if f["id"] in cand.fact_ids]
        others = [f for f in facts if f["id"] not in cand.fact_ids and f.get("status") != "planned"]
        others.sort(key=lambda f: f.get("times_featured", 0))
        fact_ctx = [{"id": f["id"], "text": f["text"], "status": f["status"]} for f in (relevant + others)[:14]]
        recent = [f"[{i.content_type}] {i.topic}" for i in recent_ideas(db, app.id, int(dd["lookback_days"]))[:10]]
        avoid_note = ""
        max_retries = int(ai_cfg["budgets"].get("max_retries", 1))
        for attempt in range(1 + max_retries):
            try:
                task = prompts.plan_task(content_type=cand.content_type, topic=cand.topic, angle_hint=cand.angle_hint,
                                         facts=fact_ctx, recent_topics=recent, screenshots=shots, video=video, tone=app.tone,
                                         audience=app.target_audience, language=app.language, cta=app.cta_text,
                                         platforms=_platforms(db, app.id), avoid_note=avoid_note)
                res = structured_call(db, purpose="content_plan", model_cls=ContentPlan, schema=PLAN_SCHEMA,
                                      system_blocks=system_with_profile(prompts.SYSTEM, prompts.profile_block(app.name, compact_profile(k, 30))),
                                      user_text=task, application_id=app.id, job_id=job_id)
            except BudgetExceeded as exc:
                if ai_cfg.get("on_budget_exceeded", "pause") == "local_fallback":
                    notes.append(f"budget exceeded -> local planner: {exc.reason}")
                    break
                raise
            except AIUnavailable as exc:
                notes.append(f"AI unavailable -> local planner: {exc}")
                break
            except AIRequestError as exc:
                if exc.retryable and attempt < max_retries:
                    say(f"Claude attempt {attempt + 1} failed ({exc}); retrying once", "warning")
                    continue
                if not exc.retryable:
                    raise
                notes.append(f"Claude failed -> local planner: {exc}")
                break
            candidate: ContentPlan = res.data
            usage_id = res.usage.id
            check = claim_check.check_plan(candidate, facts)
            sim, _ = max_script_similarity(db, app.id, candidate.narration_text(), int(dd["lookback_days"]))
            problems = []
            if not check["ok"]:
                problems.append("Fix these claim problems: " + "; ".join(i["text"] for i in check["issues"] if i["severity"] == "error")[:600])
            if sim >= float(dd["similarity_threshold"]):
                problems.append(f"The script is {int(sim * 100)}% similar to a recent video; use a different hook, structure and wording.")
            plan = candidate
            if not problems or attempt >= max_retries:
                if problems:
                    notes.append("accepted with issues after retry: " + " | ".join(problems)[:300])
                break
            avoid_note = "\nIMPORTANT: " + " ".join(problems)
            say(f"Plan rejected locally ({'; '.join(problems)[:200]}); one corrective retry", "warning")
    if plan is None:
        plan = build_local_plan(app, cand, [f for f in facts if f.get("status") != "planned"], shots, video, comfy_enabled,
                                seed=rng.randint(0, 10**6))
    _sanitize_plan(plan, {s["id"] for s in shots}, comfy_enabled)
    if fit_duration(plan, float(video["min_duration"]), float(video["max_duration"])):
        notes.append(f"scene durations scaled to {plan.duration_seconds:.0f}s to fit {video['min_duration']}-{video['max_duration']}s")
    check = claim_check.check_plan(plan, facts)
    sim, sim_id = max_script_similarity(db, app.id, plan.narration_text(), int(dd["lookback_days"]))
    if sim >= float(dd["similarity_threshold"]):
        check["issues"].append({"severity": "warning", "type": "too_similar", "text": f"{int(sim * 100)}% similar to script #{sim_id}"})
        check["warnings"] += 1
    check["similarity"] = sim

    from app.services.content.dedup import topic_fingerprint

    idea = ContentIdea(application_id=app.id, content_type=plan.content_type, topic=plan.topic[:500], angle=plan.angle,
                       fingerprint=topic_fingerprint(cand.content_type, cand.topic, cand.fact_ids), fact_ids=check["fact_ids"],
                       change_event_ids=cand.change_event_ids, source="claude" if usage_id else "local", status="planned")
    db.add(idea)
    db.flush()
    script = VideoScript(content_idea_id=idea.id, application_id=app.id, hook=plan.hook, script_text=plan.narration_text(),
                         cta=plan.cta, language=app.language, duration_seconds=plan.duration_seconds,
                         social_content=plan.social.model_dump(), claims=[c.model_dump() for c in plan.claims],
                         claim_check=check, plan_json=plan.model_dump(), ai_usage_id=usage_id)
    db.add(script)
    db.flush()
    sb = Storyboard(video_script_id=script.id, scenes=[s.model_dump() for s in plan.scenes])
    db.add(sb)
    # featuring history (drives future topic rotation without any AI call)
    now_iso = utcnow().isoformat(timespec="seconds")
    new_facts = []
    for f in k.facts or []:
        if f["id"] in check["fact_ids"]:
            f = dict(f, times_featured=f.get("times_featured", 0) + 1, last_featured_at=now_iso)
        new_facts.append(f)
    k.facts = new_facts
    for ev_id in cand.change_event_ids:
        ev = db.get(ProjectChangeEvent, ev_id)
        if ev:
            ev.promoted = True
    db.commit()
    say(f"Plan ready: {len(plan.scenes)} scenes, {plan.duration_seconds:.0f}s, source={'claude' if usage_id else 'local'}, "
        f"claims ok={check['ok']}, similarity={sim}")
    return PlanResult(idea, script, sb, bool(usage_id), notes)


_ = timedelta
