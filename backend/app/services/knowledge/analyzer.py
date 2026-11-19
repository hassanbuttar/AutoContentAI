"""Project intelligence: scan sources, detect changes incrementally, maintain the Knowledge Profile.

Token strategy: the full repository is never sent. The initial profile receives a bounded digest
(README excerpt, headings, structure, manual features). Later runs send only significant new
changes (bounded) and merge Claude's *delta* locally. No significant change -> no Claude call.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core import secrets
from app.core.paths import PathNotAllowed, check_allowed_project_path
from app.models import (Application, ApplicationKnowledge, ApplicationSource, ProjectChangeEvent, ProjectSnapshot,
                        utcnow)
from app.services.ai import prompts
from app.services.ai.client import AIUnavailable, ai_available, structured_call, system_with_profile
from app.services.ai.schemas import DELTA_SCHEMA, PROFILE_SCHEMA, DeltaOut, FactIn, ProfileOut
from app.services.knowledge import classifier as clf
from app.services.knowledge import scanner
from app.services.settings_service import get_section

log = logging.getLogger(__name__)

DOC_SNAPSHOT_LIMIT = 12_000  # chars stored per doc for later diffing
LOCAL_TYPES = {"local_folder", "local_git", "docs_folder", "changelog", "release_notes", "spec_doc"}


@dataclass
class AnalysisReport:
    sources_scanned: int = 0
    new_events: int = 0
    significant_events: int = 0
    ai_calls: int = 0
    ai_skipped_reason: str | None = None
    prompt_chars: int = 0
    tokens: int = 0
    profile_version: int | None = None
    mode: str = "incremental"
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return self.__dict__.copy()


# ------------------------------------------------------------------ change detection per source
def _add_event(db: Session, app: Application, src: ApplicationSource | None, snap: ProjectSnapshot | None, *,
               change_type: str, ref: str, title: str, details: dict, category: str, significance: float,
               user_facing: bool) -> bool:
    sp = db.begin_nested()
    try:
        db.add(ProjectChangeEvent(application_id=app.id, source_id=src.id if src else None, snapshot_id=snap.id if snap else None,
                                  change_type=change_type, ref=ref[:200], title=title[:500], details=details,
                                  category=category, significance=significance, user_facing=user_facing))
        db.flush()
        sp.commit()
        return True
    except IntegrityError:
        sp.rollback()
        return False


def _prev_snapshot(db: Session, src: ApplicationSource) -> ProjectSnapshot | None:
    return db.scalar(select(ProjectSnapshot).where(ProjectSnapshot.source_id == src.id)
                     .order_by(ProjectSnapshot.created_at.desc(), ProjectSnapshot.id.desc()).limit(1))


def _commit_events(db, app, src, snap, commits: list[dict], initial: bool) -> int:
    n = 0
    for c in reversed(commits):  # oldest first
        cat, sig, uf = clf.classify_commit(c["subject"], c.get("body", ""), c.get("files"))
        if initial:
            sig = round(sig * 0.6, 3)  # history is context, not news
        details = {"date": c.get("date"), "body": c.get("body", "")[:500],
                   "files": [f["path"] for f in (c.get("files") or [])][:20], "initial": initial}
        n += _add_event(db, app, src, snap, change_type="commit", ref=c["sha"], title=c["subject"], details=details,
                        category=cat, significance=sig, user_facing=uf)
    return n


def _doc_events(db, app, src, snap, prev: ProjectSnapshot | None, docs: dict[str, str]) -> int:
    if prev is None:
        return 0
    n = 0
    old = prev.doc_texts or {}
    for path, text in docs.items():
        before = old.get(path, "")
        if before == text[:DOC_SNAPSHOT_LIMIT]:
            continue
        added = clf.doc_added_text(before, text[:DOC_SNAPSHOT_LIMIT])
        if not added.strip():
            continue
        name = Path(path).name.lower()
        if name.startswith(("changelog", "changes", "history", "release")):
            for title, body in clf.latest_changelog_sections(text, n=2):
                if title and title not in before:
                    h = hashlib.sha1(f"{path}:{title}".encode()).hexdigest()[:16]
                    n += _add_event(db, app, src, snap, change_type="changelog", ref=h, title=f"Changelog: {title}",
                                    details={"path": path, "body": body[:2500]}, category="release", significance=0.8,
                                    user_facing=True)
            continue
        h = hashlib.sha1(f"{path}:{added}".encode()).hexdigest()[:16]
        n += _add_event(db, app, src, snap, change_type="docs", ref=h, title=f"Docs updated: {path}",
                        details={"path": path, "added": added, "headings": clf.headings(added, 10)},
                        category="docs", significance=clf.doc_change_significance(path, added), user_facing=True)
    return n


def scan_source(db: Session, app: Application, src: ApplicationSource) -> tuple[int, dict]:
    """Scan one source, store a snapshot, emit change events. Returns (new_events, context_for_profile)."""
    ctx: dict[str, Any] = {}
    allowed = get_section(db, "security")["allowed_project_roots"]
    prev = _prev_snapshot(db, src)
    new_events = 0
    cfg = src.config or {}
    if src.source_type in LOCAL_TYPES:
        path = check_allowed_project_path(src.location, allowed)
        if path.is_file():  # single changelog / spec document
            text = path.read_text(encoding="utf-8", errors="replace")[:scanner.MAX_DOC_CHARS]
            res = scanner.ScanResult(files={path.name: hashlib.sha1(text.encode()).hexdigest()}, docs={path.name: text})
        else:
            res = scanner.scan_local(path, cfg.get("include"), cfg.get("exclude"),
                                     docs_only=src.source_type in ("docs_folder", "release_notes", "spec_doc"))
        head = None
        commits: list[dict] = []
        if src.source_type in ("local_git", "local_folder") and path.is_dir() and scanner.is_git_repo(path):
            head = scanner.git_head(path, src.branch)
            if head != src.last_commit:
                commits = scanner.git_commits(path, src.last_commit, src.branch, max_count=200 if src.last_commit else 40)
            ctx["tags"] = scanner.git_tags(path, 5)
        fp = res.fingerprint()
        snap = ProjectSnapshot(application_id=app.id, source_id=src.id, commit_sha=head, fingerprint=fp,
                               manifest={"files": res.files} if len(res.files) <= 8000 else {"file_count": len(res.files)},
                               doc_texts={k: v[:DOC_SNAPSHOT_LIMIT] for k, v in list(res.docs.items())[:40]},
                               stats={"files": len(res.files), "languages": res.languages, "truncated": res.truncated})
        db.add(snap)
        db.flush()
        if commits:
            new_events += _commit_events(db, app, src, snap, commits, initial=src.last_commit is None)
        new_events += _doc_events(db, app, src, snap, prev, res.docs)
        if prev and not commits and prev.fingerprint != fp and not new_events:
            changed = [k for k, v in res.files.items() if (prev.manifest.get("files") or {}).get(k) != v][:30]
            if changed:
                cat, sig, uf = clf.classify_commit("update " + ", ".join(changed[:5]), "", [{"path": c} for c in changed])
                new_events += _add_event(db, app, src, snap, change_type="files", ref=fp[:16], title=f"{len(changed)} files changed",
                                         details={"files": changed}, category=cat, significance=min(sig, 0.4), user_facing=uf)
        src.last_commit = head or src.last_commit
        src.last_fingerprint = fp
        ctx.update({"readme": res.readme, "docs": res.docs, "tree": res.tree, "modules": res.feature_modules,
                    "manifest": res.manifest_info, "languages": res.languages})
    elif src.source_type in ("github", "gitlab"):
        token_name = f"source.{src.id}.token"
        token = secrets.get_secret(db, token_name) or secrets.get_secret(db, f"{src.source_type}.token")
        if src.source_type == "github":
            data = scanner.fetch_github(src.location, src.branch, src.last_commit, token)
        else:
            data = scanner.fetch_gitlab(src.location, src.branch, src.last_commit, token, cfg.get("base_url", "https://gitlab.com"))
        snap = ProjectSnapshot(application_id=app.id, source_id=src.id, commit_sha=data.head,
                               fingerprint=(data.head or "")[:40] or "none",
                               doc_texts={"README.md": data.readme[:DOC_SNAPSHOT_LIMIT]}, stats={"releases": len(data.releases)})
        db.add(snap)
        db.flush()
        if data.commits and data.head != src.last_commit:
            new_events += _commit_events(db, app, src, snap, data.commits, initial=src.last_commit is None)
        for rel in data.releases[:5]:
            if rel.get("prerelease"):
                continue
            new_events += _add_event(db, app, src, snap, change_type="release", ref=str(rel["tag"]),
                                     title=f"Release {rel.get('name') or rel['tag']}", details={"body": rel.get("body", "")[:2500], "date": rel.get("date")},
                                     category="release", significance=0.85 if src.last_commit else 0.5, user_facing=True)
        new_events += _doc_events(db, app, src, snap, prev, {"README.md": data.readme})
        src.last_commit = data.head or src.last_commit
        ctx.update({"readme": data.readme, "description": data.description, "topics": data.topics})
    elif src.source_type == "manual":
        text = (cfg.get("text") or "").strip()
        fp = hashlib.sha1(text.encode()).hexdigest()[:40]
        if text and fp != src.last_fingerprint:
            snap = ProjectSnapshot(application_id=app.id, source_id=src.id, fingerprint=fp, doc_texts={"manual": text[:DOC_SNAPSHOT_LIMIT]})
            db.add(snap)
            db.flush()
            added = clf.doc_added_text((prev.doc_texts or {}).get("manual", "") if prev else "", text)
            new_events += _add_event(db, app, src, snap, change_type="manual", ref=fp[:16], title="Manual feature notes updated",
                                     details={"added": added[:3000]}, category="feature", significance=0.7 if prev else 0.4,
                                     user_facing=True)
            src.last_fingerprint = fp
        ctx["manual"] = text
    elif src.source_type == "read_api":
        raise ValueError("read_api sources are configured but polling is not enabled in this version")
    src.last_scanned_at = utcnow()
    src.last_error = None
    return new_events, ctx


# ------------------------------------------------------------------ digests (context budgeting)
def _cap(text: str, n: int) -> str:
    text = (text or "").strip()
    return text if len(text) <= n else text[: n - 20] + "\n…[truncated]"


def build_initial_digest(app: Application, contexts: list[dict], budget: int) -> str:
    parts: list[str] = []
    readme = next((c.get("readme") for c in contexts if c.get("readme")), "")
    if readme:
        parts.append("## README (excerpt)\n" + _cap(readme, int(budget * 0.40)))
    for c in contexts:
        if c.get("manual"):
            parts.append("## Owner feature notes\n" + _cap(c["manual"], int(budget * 0.15)))
        if c.get("description"):
            parts.append("## Repository description\n" + c["description"][:500])
    heads: list[str] = []
    for c in contexts:
        for path, text in (c.get("docs") or {}).items():
            if path.lower().startswith("readme"):
                continue
            hs = clf.headings(text, 12)
            if hs:
                heads.append(f"{path}: " + " | ".join(hs))
    if heads:
        parts.append("## Documentation headings\n" + _cap("\n".join(heads), int(budget * 0.15)))
    for c in contexts:
        if c.get("tree"):
            parts.append("## Structure\n" + "\n".join(c["tree"][:20]))
        if c.get("modules"):
            parts.append("## Feature-like modules\n" + ", ".join(c["modules"][:60]))
        if c.get("manifest"):
            parts.append("## Manifests\n" + _cap(json.dumps(c["manifest"], ensure_ascii=False), 1200))
        if c.get("tags"):
            parts.append("## Recent tags\n" + ", ".join(t["tag"] for t in c["tags"]))
    return _cap("\n\n".join(parts), budget)


def build_change_digest(events: list[ProjectChangeEvent], budget: int) -> tuple[str, list[int]]:
    used: list[int] = []
    lines: list[str] = []
    total = 0
    for ev in sorted(events, key=lambda e: -e.significance):
        d = ev.details or {}
        body = d.get("body") or d.get("added") or ""
        files = d.get("files") or []
        chunk = f"- [{ev.category}] {ev.title}"
        if body:
            chunk += "\n  " + _cap(body, 700).replace("\n", "\n  ")
        if files:
            chunk += "\n  files: " + ", ".join(files[:8])
        if total + len(chunk) > budget:
            break
        lines.append(chunk)
        used.append(ev.id)
        total += len(chunk)
    return "\n".join(lines), used


# ------------------------------------------------------------------ facts / profile
def _next_fact_id(facts: list[dict]) -> str:
    nums = [int(f["id"][1:]) for f in facts if str(f.get("id", "")).startswith("F") and f["id"][1:].isdigit()]
    return f"F{(max(nums) + 1 if nums else 1):03d}"


def _norm(t: str) -> str:
    return " ".join("".join(ch.lower() if ch.isalnum() else " " for ch in t).split())


def add_facts(facts: list[dict], new: list[FactIn | dict], source_default: str) -> list[str]:
    added = []
    existing = {_norm(f["text"]) for f in facts}
    for nf in new:
        d = nf.model_dump() if hasattr(nf, "model_dump") else dict(nf)
        if not d.get("text") or _norm(d["text"]) in existing:
            continue
        fid = _next_fact_id(facts)
        facts.append({"id": fid, "text": d["text"].strip(), "kind": d.get("kind", "feature"), "status": d.get("status", "released"),
                      "source": d.get("source") or source_default, "evidence": d.get("evidence", "")[:300],
                      "first_seen": utcnow().isoformat(timespec="seconds"), "times_featured": 0, "last_featured_at": None,
                      "active": True})
        existing.add(_norm(d["text"]))
        added.append(fid)
    return added


def local_profile(app: Application, contexts: list[dict]) -> tuple[str, dict, list[dict]]:
    """Deterministic profile when AI is unavailable: owner-entered fields + README + doc headings."""
    facts: list[dict] = []
    add_facts(facts, [{"text": f, "kind": "feature"} for f in (app.main_features or []) if f], "owner")
    add_facts(facts, [{"text": s, "kind": "usp"} for s in (app.selling_points or []) if s], "owner")
    for c in contexts:
        manual = c.get("manual") or ""
        for line in manual.splitlines():
            line = line.strip().lstrip("-*• ").strip()
            if 8 <= len(line) <= 300:
                add_facts(facts, [{"text": line, "kind": "feature"}], "manual notes")
        readme = c.get("readme") or ""
        feat_section = False
        for line in readme.splitlines():
            s = line.strip()
            if s.startswith("#"):
                feat_section = bool(re.search(r"(?i)feature|highlights|what it does|capabilit", s))
                continue
            if feat_section and s[:2] in ("- ", "* ") and 8 <= len(s) <= 300:
                text = s[2:].replace("**", "").strip()
                add_facts(facts, [{"text": text, "kind": "feature", "evidence": "README"}], "README")
    summary = app.description or next((c.get("description") for c in contexts if c.get("description")), "") or ""
    if not summary:
        readme = next((c.get("readme") for c in contexts if c.get("readme")), "")
        paras = [p.strip() for p in readme.split("\n\n") if p.strip() and not p.strip().startswith(("#", "!", "[", "<"))]
        summary = paras[0][:600] if paras else f"{app.name} is a software application."
    profile = {"target_users": [app.target_audience] if app.target_audience else [], "unique_selling_points": list(app.selling_points or []),
               "architecture_summary": "", "marketing_opportunities": [], "known_limitations": [], "recently_shipped": []}
    return summary, profile, facts[:60]


def compact_profile(k: ApplicationKnowledge, max_facts: int = 60) -> dict:
    facts = [{"id": f["id"], "text": f["text"], "kind": f["kind"], "status": f["status"]}
             for f in (k.facts or []) if f.get("active", True)][:max_facts]
    p = k.profile or {}
    return {"summary": k.summary, "target_users": p.get("target_users", []), "usps": p.get("unique_selling_points", []),
            "facts": facts}


def _apply_delta(k: ApplicationKnowledge, delta: DeltaOut) -> dict:
    facts = list(k.facts or [])
    by_id = {f["id"]: f for f in facts}
    for u in delta.updated_facts:
        if u.id in by_id:
            by_id[u.id].update({"text": u.text, "status": u.status})
    for rid in delta.retired_fact_ids:
        if rid in by_id:
            by_id[rid]["active"] = False
    added = add_facts(facts, delta.new_facts, "changes")
    prof = dict(k.profile or {})
    if delta.recently_shipped:
        prof["recently_shipped"] = (delta.recently_shipped + prof.get("recently_shipped", []))[:15]
    if delta.marketing_opportunities:
        prof["marketing_opportunities"] = list(dict.fromkeys(delta.marketing_opportunities + prof.get("marketing_opportunities", [])))[:15]
    k.facts = facts
    k.profile = prof
    if delta.summary_update.strip():
        k.summary = delta.summary_update.strip()
    k.version += 1
    return {"added": added, "updated": [u.id for u in delta.updated_facts], "retired": delta.retired_fact_ids}


def analyze_application(db: Session, app: Application, job_id: int | None = None, force_full: bool = False,
                        log_fn=None) -> AnalysisReport:
    rep = AnalysisReport()
    say = log_fn or (lambda m, lvl="info": None)
    contexts: list[dict] = []
    for src in [s for s in app.sources if s.enabled]:
        try:
            n, ctx = scan_source(db, app, src)
            rep.sources_scanned += 1
            rep.new_events += n
            contexts.append(ctx)
            db.commit()
            say(f"Scanned {src.source_type} source #{src.id}: {n} new change events")
        except (PathNotAllowed, ValueError, RuntimeError, OSError) as exc:
            db.rollback()
            src = db.get(ApplicationSource, src.id)
            src.last_error = str(exc)[:1000]
            db.commit()
            rep.errors.append(f"source {src.id}: {exc}")
            say(f"Source #{src.id} failed: {exc}", "warning")
        except Exception as exc:  # network errors from GitHub/GitLab etc.
            db.rollback()
            src = db.get(ApplicationSource, src.id)
            src.last_error = f"{type(exc).__name__}: {exc}"[:1000]
            db.commit()
            rep.errors.append(f"source {src.id}: {type(exc).__name__}")
            say(f"Source #{src.id} failed: {type(exc).__name__}: {exc}", "warning")

    cfg = get_section(db, "ai")
    k = app.knowledge
    ok, why = ai_available(db)
    if k is None or force_full:
        rep.mode = "initial" if k is None else "full"
        if k is None:
            k = ApplicationKnowledge(application_id=app.id, version=0, facts=[], profile={})
            app.knowledge = k
            db.flush()
        digest = build_initial_digest(app, contexts, int(cfg["profile_char_budget"]))
        app_fields = {"name": app.name, "description": app.description, "category": app.category,
                      "target_audience": app.target_audience, "main_features": app.main_features,
                      "selling_points": app.selling_points, "website": app.website_url}
        summary, profile, facts = local_profile(app, contexts)
        used_ai = False
        if ok:
            try:
                res = structured_call(db, purpose="profile_initial", model_cls=ProfileOut, schema=PROFILE_SCHEMA,
                                      system_blocks=system_with_profile(prompts.SYSTEM, None),
                                      user_text=prompts.initial_profile_task(app_fields, digest),
                                      application_id=app.id, job_id=job_id, max_tokens=8000)
                po: ProfileOut = res.data
                rep.ai_calls += 1
                rep.prompt_chars += res.prompt_chars
                rep.tokens += res.usage.input_tokens + res.usage.output_tokens
                summary = po.summary
                profile = {"target_users": po.target_users, "unique_selling_points": po.unique_selling_points,
                           "architecture_summary": po.architecture_summary, "marketing_opportunities": po.marketing_opportunities,
                           "known_limitations": po.known_limitations, "recently_shipped": po.recently_shipped}
                ai_facts: list[dict] = []
                add_facts(ai_facts, po.facts, "analysis")
                # keep owner-entered facts first; they are authoritative
                owner = [f for f in facts if f["source"] == "owner"]
                facts = owner[:]
                add_facts(facts, [dict(f, text=f["text"]) for f in ai_facts], "analysis")
                used_ai = True
            except AIUnavailable as exc:
                rep.ai_skipped_reason = str(exc)
        else:
            rep.ai_skipped_reason = why
        old_by_text = {_norm(f["text"]): f for f in (k.facts or [])}
        for f in facts:  # preserve featuring history across full rebuilds
            prev = old_by_text.get(_norm(f["text"]))
            if prev:
                f.update({"times_featured": prev.get("times_featured", 0), "last_featured_at": prev.get("last_featured_at")})
        k.summary, k.profile, k.facts = summary, profile, facts
        k.generated_by = "claude" if used_ai else "local"
        k.version += 1
        k.last_full_analysis_at = utcnow()
        # all existing events are now reflected
        for ev in db.scalars(select(ProjectChangeEvent).where(ProjectChangeEvent.application_id == app.id,
                                                              ProjectChangeEvent.processed.is_(False))):
            ev.processed = True
        rep.prompt_chars = rep.prompt_chars or len(digest)
    else:
        pending = list(db.scalars(select(ProjectChangeEvent).where(ProjectChangeEvent.application_id == app.id,
                                                                   ProjectChangeEvent.processed.is_(False))))
        threshold = float(cfg["significance_threshold"])
        significant = [e for e in pending if e.significance >= threshold]
        rep.significant_events = len(significant)
        if not significant:
            rep.ai_skipped_reason = "no significant changes" if pending else "no new changes"
            for e in pending:
                e.processed = True
        elif not ok:
            rep.ai_skipped_reason = why
            # local merge: significant feature-like commit subjects become 'recently shipped' notes (not facts)
            prof = dict(k.profile or {})
            prof["recently_shipped"] = ([e.title for e in significant if e.category in ("feature", "improvement", "release")]
                                        + prof.get("recently_shipped", []))[:15]
            k.profile = prof
            k.version += 1
            for e in pending:
                e.processed = True
        else:
            digest, used_ids = build_change_digest(significant, int(cfg["digest_char_budget"]))
            res = structured_call(db, purpose="profile_update", model_cls=DeltaOut, schema=DELTA_SCHEMA,
                                  system_blocks=system_with_profile(prompts.SYSTEM, prompts.profile_block(app.name, compact_profile(k))),
                                  user_text=prompts.delta_task(digest), application_id=app.id, job_id=job_id, max_tokens=4000)
            rep.ai_calls += 1
            rep.prompt_chars += res.prompt_chars
            rep.tokens += res.usage.input_tokens + res.usage.output_tokens
            if res.data.meaningful:
                merged = _apply_delta(k, res.data)
                say(f"Profile updated: {merged}")
            for e in pending:
                if e.id in used_ids or e.significance < threshold:
                    e.processed = True
        k.last_incremental_at = utcnow()
    rep.profile_version = k.version
    db.commit()
    return rep
