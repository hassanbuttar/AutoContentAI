"""Deterministic 'local model' for change classification. No AI calls.

Scores how marketing-relevant a change is (0..1) using conventional-commit prefixes,
keywords and touched paths. Only changes above the configured threshold are sent to Claude.
"""
from __future__ import annotations

import difflib
import re

CONVENTIONAL = re.compile(r"^(?P<type>[a-zA-Z]+)(?:\((?P<scope>[^)]*)\))?(?P<bang>!)?:\s*(?P<desc>.+)$")
TYPE_SCORES = {
    "feat": ("feature", 0.8), "feature": ("feature", 0.8), "perf": ("improvement", 0.6), "ui": ("improvement", 0.6),
    "ux": ("improvement", 0.6), "fix": ("fix", 0.35), "bugfix": ("fix", 0.35), "hotfix": ("fix", 0.35),
    "docs": ("docs", 0.2), "doc": ("docs", 0.2), "release": ("release", 0.85),
    "refactor": ("chore", 0.08), "chore": ("chore", 0.03), "ci": ("chore", 0.0), "build": ("chore", 0.02),
    "test": ("chore", 0.0), "tests": ("chore", 0.0), "style": ("chore", 0.02), "revert": ("chore", 0.1), "deps": ("chore", 0.0),
}
FEATURE_WORDS = re.compile(r"(?i)\b(add(?:s|ed)?|new|introduc\w*|launch\w*|support(?:s)? for|implement\w*|enable\w*|allow\w*|integrat\w*)\b")
IMPROVE_WORDS = re.compile(r"(?i)\b(improv\w*|faster|speed\w*|optimi[sz]\w*|redesign\w*|enhanc\w*|better|simplif\w*|revamp\w*|polish\w*)\b")
FIX_WORDS = re.compile(r"(?i)\b(fix\w*|bug\w*|crash\w*|issue|error|resolve\w*|patch\w*)\b")
NOISE = re.compile(r"(?i)\b(bump|dependabot|renovate|lockfile|lint|typo|format(?:ting)?|merge branch|wip|readme badge|version bump|prettier|eslint)\b")
CHORE_PATHS = re.compile(r"(?i)(^|/)(tests?|__tests__|spec|\.github|\.circleci|ci|scripts|docker)/|\.(lock|snap)$|(^|/)(package-lock\.json|yarn\.lock|pnpm-lock\.yaml|poetry\.lock|go\.sum|Cargo\.lock)$")
UI_PATHS = re.compile(r"(?i)(^|/)(pages|screens|views|components|ui|app|routes|features|templates|frontend|web)/")


def classify_commit(subject: str, body: str = "", files: list[dict] | None = None) -> tuple[str, float, bool]:
    subject = (subject or "").strip()
    files = files or []
    category, score = "other", 0.25
    m = CONVENTIONAL.match(subject)
    text = subject + " " + (body or "")[:300]
    if m:
        category, score = TYPE_SCORES.get(m.group("type").lower(), ("other", 0.2))
        if m.group("bang"):
            score += 0.1
    else:
        if FEATURE_WORDS.search(subject):
            category, score = "feature", 0.6
        elif IMPROVE_WORDS.search(subject):
            category, score = "improvement", 0.5
        elif FIX_WORDS.search(subject):
            category, score = "fix", 0.3
    if NOISE.search(text):
        category, score = "chore", min(score, 0.03)
    paths = [f.get("path", "") for f in files]
    if paths and all(CHORE_PATHS.search(p) for p in paths):
        category, score = "chore", min(score, 0.05)
    user_facing = any(UI_PATHS.search(p) for p in paths)
    if user_facing and category in ("feature", "improvement"):
        score += 0.1
    churn = sum(f.get("added", 0) + f.get("deleted", 0) for f in files)
    if category in ("feature", "improvement") and churn > 300:
        score += 0.05
    if category in ("feature", "improvement", "release"):
        user_facing = True
    return category, round(max(0.0, min(1.0, score)), 3), user_facing


HEADING = re.compile(r"(?m)^\s{0,3}(#{1,4})\s+(.+?)\s*#*\s*$")


def doc_added_text(old: str, new: str, limit: int = 2500) -> str:
    """Only lines added/changed in a doc (keeps prompts small)."""
    added = [ln[1:] for ln in difflib.unified_diff(old.splitlines(), new.splitlines(), lineterm="", n=0)
             if ln.startswith("+") and not ln.startswith("+++")]
    out = "\n".join(a for a in added if a.strip())
    return out[:limit]


def headings(text: str, limit: int = 40) -> list[str]:
    return [m.group(2).strip()[:120] for m in HEADING.finditer(text or "")][:limit]


def latest_changelog_sections(text: str, n: int = 2, limit: int = 3000) -> list[tuple[str, str]]:
    """Split a Keep-a-Changelog style file into (heading, body) for the newest n version sections.
    A section runs until the next *version* heading, so sub-headings like "### Added" stay inside it."""
    version_re = re.compile(r"\d+\.\d+|unreleased|\d{4}-\d{2}-\d{2}", re.I)
    heads = [m for m in re.finditer(r"(?m)^\s{0,3}#{1,3}\s+(.+)$", text or "") if version_re.search(m.group(1))]
    parts: list[tuple[str, str]] = []
    for i, m in enumerate(heads[:n]):
        end = heads[i + 1].start() if i + 1 < len(heads) else len(text)
        parts.append((m.group(1).strip()[:200], text[m.end():end].strip()[:limit]))
    return parts


def doc_change_significance(path: str, added: str) -> float:
    if not added.strip():
        return 0.0
    base = 0.25
    if re.search(r"(?i)(feature|guide|usage|how to|tutorial|release|what'?s new)", path + " " + added[:500]):
        base += 0.15
    if len(added) > 800:
        base += 0.05
    return round(min(base, 0.6), 3)
