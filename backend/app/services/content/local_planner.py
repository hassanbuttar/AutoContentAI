"""Deterministic template planner. Used in cost-saving mode, when the AI budget is exhausted,
when no API key is configured, or as a fallback. Uses only verified facts."""
from __future__ import annotations

import random
import re

from app.models import Application
from app.services.ai.schemas import ContentPlan
from app.services.content.dedup import tokens
from app.services.content.topics import TopicCandidate

HOOKS = {
    "feature_announcement": ["New in {app}: {short}", "{app} just got better", "Have you seen this in {app}?"],
    "release_highlights": ["What's new in {app}", "Fresh update: {short}", "{app} update, in 30 seconds"],
    "problem_solution": ["Still doing this the hard way?", "Tired of wasting time on this?", "There's an easier way"],
    "benefits_use_cases": ["Here's why people use {app}", "3 things {app} does for you", "Work smarter with {app}"],
    "tips_tutorial": ["Quick {app} tip", "You're probably missing this in {app}", "Try this in {app} today"],
    "product_demo": ["See {app} in action", "This is {app}", "Watch how {app} works"],
    "productivity_tip": ["A small change that saves time", "Productivity tip of the day", "Work less, do more"],
    "educational": ["Did you know?", "Quick lesson", "Let's break it down"],
    "walkthrough": ["A quick tour of {app}", "Let me show you around {app}", "{app} in under a minute"],
    "comparison": ["Before vs. after {app}", "The old way vs. {app}", "See the difference"],
    "evergreen": ["Meet {app}", "Why {app}?", "Say hello to {app}"],
}
STYLE = ("clean modern 3D illustration, soft studio lighting, vibrant gradient background in {colors}, "
         "minimalist, high detail, vertical composition, no text, no logos")


def _sentence(text: str) -> str:
    t = re.sub(r"\s+", " ", text.strip().rstrip("."))
    if not t:
        return ""
    return t[0].upper() + t[1:] + ("" if t[-1] in "?!" else ".")


def _short(text: str, words: int = 6) -> str:
    w = re.sub(r"[^\w\s'?!-]", "", text).split()
    out = " ".join(w[:words])
    return out + ("…" if len(w) > words else "")


def _visual_prompt(fact_text: str, colors: str) -> str:
    keys = ", ".join(tokens(fact_text)[:6]) or "software productivity"
    return f"Conceptual scene representing {keys}. " + STYLE.format(colors=colors)


def build_local_plan(app: Application, cand: TopicCandidate, facts: list[dict], screenshots: list[dict], video: dict,
                     comfy_enabled: bool, seed: int | None = None) -> ContentPlan:
    rng = random.Random(seed)
    by_id = {f["id"]: f for f in facts}
    chosen = [by_id[i] for i in cand.fact_ids if i in by_id]
    others = [f for f in facts if f["id"] not in cand.fact_ids and f.get("status") == "released"
              and f.get("kind") in ("feature", "benefit", "usp", "integration")]
    rng.shuffle(others)
    n_scenes = max(3, min(int(video.get("scenes", 5)), 8))
    body_facts = (chosen + others)[: n_scenes - 2] or [{"id": "", "text": app.description or app.name, "status": "released"}]
    colors = " and ".join((app.brand_colors or ["deep indigo", "cyan"])[:2])
    # Keep the topic whole when it is short enough; never cut a phrase mid-way inside the hook.
    short_topic = cand.topic.strip().rstrip(".") if len(cand.topic.split()) <= 7 else _short(cand.topic, 5)
    hook = rng.choice(HOOKS.get(cand.content_type, HOOKS["evergreen"])).format(app=app.name, short=short_topic)
    hook_overlay = hook if len(hook.split()) <= 9 else hook.split(":")[0]
    scenes = []
    scenes.append({"duration_seconds": 3.5, "narration": _sentence(hook), "overlay_text": hook_overlay[:90],
                   "visual_source": "comfyui" if comfy_enabled else "title_card",
                   "visual_prompt": _visual_prompt(cand.topic, colors), "motion": "zoom_in",
                   "fact_ids": cand.fact_ids[:1]})
    shots = list(screenshots)
    rng.shuffle(shots)
    for i, f in enumerate(body_facts):
        text = f["text"]
        beta = " It's in beta." if f.get("status") == "beta" else ""
        # Templates keep the fact's own wording and casing (facts are often noun phrases with proper nouns),
        # so no lead ever lowercases or re-conjugates them.
        pattern = rng.choice(["{fact}.", "{fact}. Built right into {app}.", "Here's the best part: {fact}.", "New in {app}: {fact}."
                              if cand.content_type in ("feature_announcement", "release_highlights") else "In {app}: {fact}."])
        narration = pattern.format(app=app.name, fact=re.sub(r"\s+", " ", text.strip().rstrip("."))) + beta
        words = len(narration.split())
        dur = round(max(3.5, min(9.0, words / 2.4 + 0.8)), 1)
        if shots:
            s = shots[i % len(shots)]
            src = {"visual_source": "application_screenshot", "screenshot_asset_id": s["id"], "visual_prompt": ""}
        elif comfy_enabled:
            src = {"visual_source": "comfyui", "visual_prompt": _visual_prompt(text, colors)}
        else:
            src = {"visual_source": "title_card", "visual_prompt": ""}
        scenes.append({"duration_seconds": dur, "narration": narration, "overlay_text": _short(text, 6),
                       "motion": rng.choice(["zoom_in", "zoom_out", "pan_left", "pan_right"]), "fact_ids": [f["id"]] if f["id"] else [], **src})
    cta = app.cta_text or f"Try {app.name} today"
    scenes.append({"duration_seconds": 4.0, "narration": _sentence(cta), "overlay_text": _short(cta, 7),
                   "visual_source": "title_card", "visual_prompt": "", "motion": "zoom_in", "fact_ids": []})
    tags = ["#" + re.sub(r"\W", "", app.name)]
    if app.category:
        tags.append("#" + re.sub(r"\W", "", app.category.title()))
    tags += ["#" + w.capitalize() for w in tokens(cand.topic)[:3]] + ["#Productivity", "#Software"]
    tags = list(dict.fromkeys(t for t in tags if len(t) > 2))[:8]
    caption = f"{hook}\n{_sentence(body_facts[0]['text'])}\n{cta}"
    all_ids = [f["id"] for f in body_facts if f.get("id")]
    plan = {
        "content_type": cand.content_type, "topic": cand.topic, "angle": cand.angle_hint or cand.reason,
        "target_audience": app.target_audience, "hook": hook, "duration_seconds": 30, "fact_ids": all_ids,
        "scenes": scenes, "cta": cta,
        "social": {"instagram": {"caption": caption, "hashtags": tags}, "tiktok": {"caption": f"{hook} {cta}", "hashtags": tags[:5]},
                   "youtube": {"title": f"{hook}"[:90], "description": f"{_sentence(body_facts[0]['text'])} {cta}",
                               "tags": [t.lstrip('#') for t in tags]}},
        "claims": [{"text": f["text"], "fact_ids": [f["id"]]} for f in body_facts if f.get("id")],
    }
    return ContentPlan.model_validate(plan)
