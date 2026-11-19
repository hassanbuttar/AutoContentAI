"""Claim verification against the Knowledge Profile (local, deterministic)."""
from __future__ import annotations

import re

from app.services.ai.schemas import ContentPlan

RISKY = [
    (re.compile(r"(?i)(?<![\w#])(#\s?1(?!\d)|number one\b|best in the world|world'?s best\b|industry[- ]leading|market[- ]leading)"), "superlative ranking claim"),
    (re.compile(r"(?i)\b(guarantee[sd]?|100\s?%|risk[- ]free|never fails)\b"), "absolute guarantee"),
    (re.compile(r"(?i)\b\d{2,3}\s?%\s?(faster|cheaper|more|less|better|increase|boost)"), "unverified percentage"),
    (re.compile(r"(?i)\b(\d[\d,.]*\s?(k|m|million|thousand)?\+?\s+(users|customers|downloads|companies))\b"), "unverified user count"),
    (re.compile(r"(?i)\b(coming soon|launching soon|next month)\b"), "unreleased feature language"),
    (re.compile(r"(?i)\b(free forever|free trial|discount|\$\s?\d)"), "pricing claim (verify manually)"),
]


def check_plan(plan: ContentPlan, facts: list[dict]) -> dict:
    by_id = {f["id"]: f for f in facts if f.get("active", True)}
    issues: list[dict] = []
    referenced: set[str] = set(plan.fact_ids)
    for c in plan.claims:
        referenced |= set(c.fact_ids)
        if not c.fact_ids:
            issues.append({"severity": "warning", "type": "uncited_claim", "text": c.text})
    for s in plan.scenes:
        referenced |= set(s.fact_ids)
    for fid in sorted(referenced):
        f = by_id.get(fid)
        if f is None:
            issues.append({"severity": "error", "type": "unknown_fact", "text": f"Fact {fid} does not exist"})
        elif f.get("status") == "planned":
            issues.append({"severity": "error", "type": "unreleased_feature", "text": f"Fact {fid} is planned, not released: {f['text']}"})
        elif f.get("status") == "beta":
            text = " ".join([plan.hook, plan.narration_text()]).lower()
            if "beta" not in text and "preview" not in text and "early access" not in text:
                issues.append({"severity": "warning", "type": "beta_not_labelled", "text": f"Fact {fid} is beta; mention it is beta"})
    corpus = " \n".join([plan.hook, plan.narration_text(), plan.cta, " ".join(s.overlay_text for s in plan.scenes),
                         plan.social.instagram.caption, plan.social.tiktok.caption, plan.social.youtube.title,
                         plan.social.youtube.description])
    fact_text = " ".join(f["text"] for f in by_id.values()).lower()
    for pat, label in RISKY:
        for m in pat.finditer(corpus):
            if m.group(0).lower() not in fact_text:
                issues.append({"severity": "error" if label != "pricing claim (verify manually)" else "warning",
                               "type": "risky_language", "text": f"{label}: “{m.group(0)}”"})
    if not referenced and by_id:
        issues.append({"severity": "warning", "type": "no_fact_references", "text": "Plan does not reference any facts"})
    errors = [i for i in issues if i["severity"] == "error"]
    return {"ok": not errors, "errors": len(errors), "warnings": len(issues) - len(errors), "issues": issues[:50],
            "fact_ids": sorted(referenced)}
