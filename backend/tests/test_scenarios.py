"""Acceptance scenarios 1-10 from the specification."""
from __future__ import annotations

import json
from datetime import datetime, timedelta

import httpx
import pytest
from sqlalchemy import func, select

from tests.conftest import allow_root, git, run_all_jobs, run_job


def _mk_app(dbs, name="TaskFlow", **kw):
    from app.models import Application

    a = Application(name=name, slug=name.lower().replace(" ", "-"), description=kw.pop("description", f"{name} helps teams."),
                    main_features=kw.pop("main_features", ["Drag-and-drop sprint board", "Slack notifications for task updates",
                                                           "AI standup summaries"]),
                    brand_colors=["#4F46E5", "#0EA5E9"], **kw)
    dbs.add(a)
    dbs.commit()
    return a


# ---------------------------------------------------------------- Scenario 1
def test_s1_register_repo_builds_knowledge_profile(client, dbs, repo, fake_claude):
    allow_root(dbs, repo["root"])
    a = client.post("/api/applications", json={"name": "TaskFlow", "description": "Sprint planning"}).json()
    r = client.post(f"/api/applications/{a['id']}/sources", json={"source_type": "local_git", "location": str(repo["path"])})
    assert r.status_code == 201, r.text
    assert r.json()["is_git"] is True
    job_id = client.post(f"/api/applications/{a['id']}/analyze").json()["job_id"]
    job = run_job(job_id)
    assert job.status == "succeeded", job.last_error
    k = client.get(f"/api/applications/{a['id']}/knowledge").json()["knowledge"]
    texts = [f["text"] for f in k["facts"]]
    assert "Drag-and-drop sprint board" in texts
    planned = [f for f in k["facts"] if f["status"] == "planned"]
    assert planned and planned[0]["text"] == "Mobile app"
    assert k["generated_by"] == "claude"
    # repository text was wrapped as untrusted data
    prompt = fake_claude.requests[0]["messages"][0]["content"]
    assert "<project_data>" in prompt and "Ignore previous instructions" in prompt.split("<project_data>")[1]
    assert "untrusted data" in fake_claude.requests[0]["system"][0]["text"]


def test_s1_paths_outside_allowed_roots_rejected(client, dbs, repo):
    a = client.post("/api/applications", json={"name": "X"}).json()
    r = client.post(f"/api/applications/{a['id']}/sources", json={"source_type": "local_folder", "location": str(repo["path"])})
    assert r.status_code == 400 and "allowed" in r.text.lower()


# ---------------------------------------------------------------- Scenario 2
def test_s2_incremental_analysis_sends_only_changes(client, dbs, repo, fake_claude):
    allow_root(dbs, repo["root"])
    a = client.post("/api/applications", json={"name": "TaskFlow"}).json()
    client.post(f"/api/applications/{a['id']}/sources", json={"source_type": "local_git", "location": str(repo["path"])})
    assert run_job(client.post(f"/api/applications/{a['id']}/analyze").json()["job_id"]).status == "succeeded"
    first_prompt = fake_claude.requests[-1]["messages"][0]["content"]
    # change the repository
    (repo["path"] / "src" / "features" / "theme").mkdir(parents=True)
    (repo["path"] / "src" / "features" / "theme" / "dark.py").write_text("DARK = True\n")
    git(repo["path"], "add", ".")
    git(repo["path"], "commit", "-q", "-m", "feat(ui): add dark mode for the whole dashboard")
    n_before = len(fake_claude.requests)
    assert run_job(client.post(f"/api/applications/{a['id']}/analyze").json()["job_id"]).status == "succeeded"
    assert len(fake_claude.requests) == n_before + 1
    delta_prompt = fake_claude.requests[-1]["messages"][0]["content"]
    assert "dark mode" in delta_prompt
    assert "TaskFlow helps small teams plan sprints" not in delta_prompt  # README not resent
    assert len(delta_prompt) < len(first_prompt)
    # profile goes into the cacheable system block, not the user message
    assert fake_claude.requests[-1]["system"][-1].get("cache_control") == {"type": "ephemeral"}
    k = client.get(f"/api/applications/{a['id']}/knowledge").json()
    assert any("Dark mode" in f["text"] for f in k["knowledge"]["facts"])
    # no changes -> no Claude call at all
    n_before = len(fake_claude.requests)
    job = run_job(client.post(f"/api/applications/{a['id']}/analyze").json()["job_id"])
    assert len(fake_claude.requests) == n_before
    assert job.result["ai_skipped_reason"] in ("no new changes", "no significant changes")


def test_s2_chore_commits_do_not_trigger_ai(client, dbs, repo, fake_claude):
    allow_root(dbs, repo["root"])
    a = client.post("/api/applications", json={"name": "TaskFlow"}).json()
    client.post(f"/api/applications/{a['id']}/sources", json={"source_type": "local_git", "location": str(repo["path"])})
    run_job(client.post(f"/api/applications/{a['id']}/analyze").json()["job_id"])
    (repo["path"] / "package-lock.json").write_text("{}")
    git(repo["path"], "add", ".")
    git(repo["path"], "commit", "-q", "-m", "chore(deps): bump lodash")
    n = len(fake_claude.requests)
    job = run_job(client.post(f"/api/applications/{a['id']}/analyze").json()["job_id"])
    assert len(fake_claude.requests) == n and job.result["new_events"] == 1


# ---------------------------------------------------------------- Scenario 3 + 9
def test_s3_s9_generate_playable_mp4_with_comfy_down(client, dbs, fake_claude):
    from app.services.settings_service import update_section
    from app.services.video.ffmpeg import probe
    from app.core.paths import storage_path
    from app.models import LogEntry, RenderedVideo

    update_section(dbs, "comfyui", {"url": "http://127.0.0.1:9", "enabled": True})  # nothing listens on port 9
    update_section(dbs, "video", {"voice": {"enabled": False}})
    dbs.commit()
    a = client.post("/api/applications", json={"name": "TaskFlow", "main_features": ["Drag-and-drop sprint board"],
                                                "brand_colors": ["#4F46E5", "#0EA5E9"]}).json()
    job_id = client.post("/api/studio/generate", json={"application_id": a["id"]}).json()["job_id"]
    jobs = run_all_jobs()
    assert all(j.status == "succeeded" for j in jobs), [(j.job_type, j.last_error) for j in jobs]
    v = dbs.scalar(select(RenderedVideo).where(RenderedVideo.application_id == a["id"]))
    assert v.status == "ready", v.error
    pr = probe(storage_path(v.path))
    assert (pr.width, pr.height) == (1080, 1920) and pr.vcodec == "h264" and pr.acodec == "aac"
    assert pr.duration > 5
    assert v.qc_report["ok"]
    msgs = [e.message for e in dbs.scalars(select(LogEntry))]
    assert any("ComfyUI unavailable" in m and "Start ComfyUI" in m for m in msgs), msgs  # actionable error
    # the dashboard can stream it
    r = client.get(f"/api/videos/{v.id}/file", headers={"Range": "bytes=0-99"})
    assert r.status_code == 206 and len(r.content) == 100
    assert job_id


# ---------------------------------------------------------------- Scenario 4
def test_s4_independent_schedules(dbs):
    from app.models import PublicationRecord, PublishingRule, SocialAccount
    from app.services.scheduling.engine import materialize

    a1, a2 = _mk_app(dbs, "Alpha"), _mk_app(dbs, "Beta")
    def acc(app, platform):
        x = SocialAccount(application_id=app.id, platform=platform, display_name=f"{app.name}-{platform}", status="export_only")
        dbs.add(x)
        dbs.flush()
        return x
    rules = [
        PublishingRule(application_id=a1.id, social_account_id=acc(a1, "tiktok").id, posts_per_period=2, timezone="Europe/London"),
        PublishingRule(application_id=a1.id, social_account_id=acc(a1, "instagram").id, posts_per_period=1, posting_times=["18:00"]),
        PublishingRule(application_id=a2.id, social_account_id=acc(a2, "tiktok").id, posts_per_period=1, timezone="Asia/Karachi"),
        PublishingRule(application_id=a2.id, social_account_id=acc(a2, "instagram").id, posts_per_period=2),
        PublishingRule(application_id=a2.id, social_account_id=acc(a2, "youtube").id, frequency="weekly", posts_per_period=3),
    ]
    dbs.add_all(rules)
    dbs.commit()
    now = datetime(2026, 11, 16, 0, 0)  # Monday
    materialize(dbs, now, 7)
    def count(rule):
        return dbs.scalar(select(func.count()).select_from(PublicationRecord).where(
            PublicationRecord.publishing_rule_id == rule.id, PublicationRecord.scheduled_for >= now,
            PublicationRecord.scheduled_for < now + timedelta(days=7)))
    assert [count(r) for r in rules] in ([14, 7, 7, 14, 3], [14, 7, 7, 14, 3])
    # pausing one account affects only its own future slots
    acc_obj = dbs.get(SocialAccount, rules[0].social_account_id)
    acc_obj.paused = True
    dbs.commit()
    materialize(dbs, now, 7)
    planned = dbs.scalar(select(func.count()).select_from(PublicationRecord).where(
        PublicationRecord.publishing_rule_id == rules[0].id, PublicationRecord.status == "planned"))
    assert planned == 0
    assert count(rules[1]) == 7


# ---------------------------------------------------------------- Scenario 5 + 8
class RecordingAdapter:
    platform = "youtube"
    display_name = "Recording"

    def __init__(self, verify_status="published"):
        self.publish_calls = 0
        self.verify_calls = 0
        self.verify_status = verify_status

    def capabilities(self, s):
        return {"direct_publish": True}

    def preflight(self, *a):
        return []

    def publish(self, ctx):
        from app.services.publishing.base import PublishResult

        self.publish_calls += 1
        ctx.save_state({"video_id": f"vid{self.publish_calls}"})
        return PublishResult("published", f"vid{self.publish_calls}", "https://example.invalid/v")

    def verify(self, ctx):
        from app.services.publishing.base import PublishResult

        self.verify_calls += 1
        return PublishResult(self.verify_status, ctx.state.get("video_id"))


def _ready_publication(dbs, tmp, status="approved"):
    from app.core import secrets
    from app.core.config import get_settings
    from app.models import PublicationRecord, RenderedVideo, SocialAccount
    from app.services.publishing.base import TokenSet
    from app.services.publishing.service import token_secret_name

    app = _mk_app(dbs, "Pubber")
    acc = SocialAccount(application_id=app.id, platform="youtube", display_name="YT", status="connected")
    dbs.add(acc)
    dbs.flush()
    secrets.set_secret(dbs, token_secret_name(acc.id), TokenSet("tok", "ref", datetime(2099, 1, 1)).to_dict())
    vdir = get_settings().storage("videos", "pubber")
    (vdir / "v.mp4").write_bytes(b"\x00" * 20000)
    v = RenderedVideo(application_id=app.id, status="ready", review_status="approved", path="videos/pubber/v.mp4",
                      width=1080, height=1920, duration=20, size_bytes=20000, fps=30, renderer="title_card")
    dbs.add(v)
    dbs.flush()
    p = PublicationRecord(application_id=app.id, social_account_id=acc.id, platform="youtube", status=status,
                          scheduled_for=datetime.utcnow() - timedelta(minutes=1), idempotency_key="test:1", rendered_video_id=v.id)
    dbs.add(p)
    dbs.commit()
    return p


def test_s8_same_job_twice_publishes_once(env, dbs):
    from app.services.publishing import registry
    from app.services.publishing.service import publish_publication
    from app.workers.queue import enqueue

    ad = RecordingAdapter()
    registry._override["youtube"] = ad
    p = _ready_publication(dbs, env["tmp"])
    j1, c1 = enqueue(dbs, "publish", {"publication_id": p.id}, idempotency_key=f"publish:{p.id}")
    j2, c2 = enqueue(dbs, "publish", {"publication_id": p.id}, idempotency_key=f"publish:{p.id}")
    dbs.commit()
    assert c1 and not c2 and j1.id == j2.id
    r1 = publish_publication(dbs, p.id)
    r2 = publish_publication(dbs, p.id)  # e.g. a retry or a second worker
    assert r1["status"] == "published" and r2.get("skipped")
    assert ad.publish_calls == 1


def test_s5_crash_mid_publish_is_verified_not_reposted(env, dbs):
    from app.services.publishing import registry
    from app.services.publishing.service import publish_publication

    ad = RecordingAdapter(verify_status="published")
    registry._override["youtube"] = ad
    p = _ready_publication(dbs, env["tmp"], status="publishing")  # process died after starting the upload
    p.platform_state = {"video_id": "vid-before-crash"}
    dbs.commit()
    res = publish_publication(dbs, p.id)
    dbs.refresh(p)
    assert ad.publish_calls == 0 and ad.verify_calls == 1
    assert p.status == "published" and p.external_post_id == "vid-before-crash" and res["verified"]


def test_s5_unknown_outcome_requires_human(env, dbs):
    from app.services.publishing import registry
    from app.services.publishing.service import publish_publication

    registry._override["youtube"] = RecordingAdapter(verify_status="unknown")
    p = _ready_publication(dbs, env["tmp"], status="publishing")
    publish_publication(dbs, p.id)
    dbs.refresh(p)
    assert p.status == "verify_required"


def test_s5_restart_recovers_jobs_and_slots(env, dbs):
    from app.models import PublicationRecord, PublishingRule, ScheduledJob, SocialAccount
    from app.services.scheduling.engine import tick
    from app.workers import queue as q

    a = _mk_app(dbs, "Recov")
    acc = SocialAccount(application_id=a.id, platform="tiktok", display_name="T", status="export_only")
    dbs.add(acc)
    dbs.flush()
    dbs.add(PublishingRule(application_id=a.id, social_account_id=acc.id, posts_per_period=2, generation_lead_hours=48))
    dbs.commit()
    now = datetime.utcnow()
    tick(dbs, now)
    pubs1 = dbs.scalar(select(func.count(PublicationRecord.id)))
    jobs1 = dbs.scalar(select(func.count(ScheduledJob.id)))
    # "restart": tick again (and again) -> nothing duplicated
    tick(dbs, now)
    tick(dbs, now + timedelta(seconds=60))
    assert dbs.scalar(select(func.count(PublicationRecord.id))) == pubs1
    assert dbs.scalar(select(func.count(ScheduledJob.id))) == jobs1
    # a job that was running in a process that no longer exists is recovered
    job = dbs.scalar(select(ScheduledJob).limit(1))
    import socket

    job.status, job.locked_by, job.lease_expires_at = "running", f"{socket.gethostname()}:999999:default-0", now + timedelta(hours=1)
    job.attempts = 1
    dbs.commit()
    recovered = q.recover_dead_workers_on_startup(dbs, socket.gethostname())
    dbs.refresh(job)
    assert job.id in [r.id for r in recovered] and job.status == "queued"


# ---------------------------------------------------------------- Scenario 6 (official API contract, mocked transport)
def test_s6_oauth_connect_and_youtube_publish_contract(client, dbs, env):
    from app.models import SocialAccount
    from app.services.publishing import registry
    from app.services.publishing.youtube import YouTubeAdapter

    calls = []

    def handler(request: httpx.Request):
        calls.append((request.method, str(request.url)))
        u = str(request.url)
        if u.startswith("https://oauth2.googleapis.com/token"):
            body = dict(httpx.QueryParams(request.content.decode()))
            assert body["grant_type"] == "authorization_code" and body["code_verifier"]
            return httpx.Response(200, json={"access_token": "ya29.test", "refresh_token": "1//r", "expires_in": 3600, "scope": "upload"})
        if "/youtube/v3/channels" in u:
            return httpx.Response(200, json={"items": [{"id": "UC123", "snippet": {"title": "TaskFlow"}}]})
        if u.startswith("https://www.googleapis.com/upload/youtube/v3/videos"):
            meta = json.loads(request.content)
            assert meta["status"]["containsSyntheticMedia"] is False
            assert request.headers["X-Upload-Content-Length"]
            return httpx.Response(200, headers={"Location": "https://upload.example/session1"})
        if u == "https://upload.example/session1":
            return httpx.Response(201, json={"id": "abc123"})
        return httpx.Response(404)

    registry._override["youtube"] = YouTubeAdapter(httpx.Client(transport=httpx.MockTransport(handler)))
    client.patch("/api/settings/oauth", json={"youtube": {"client_id": "cid.apps.googleusercontent.com"}})
    client.put("/api/secrets", json={"name": "oauth.youtube.client_secret", "value": "shh-secret"})
    app_id = client.post("/api/applications", json={"name": "TaskFlow"}).json()["id"]
    acc = client.post("/api/accounts", json={"application_id": app_id, "platform": "youtube", "display_name": "TF YT"}).json()
    start = client.post(f"/api/accounts/{acc['id']}/oauth/start").json()
    assert "code_challenge=" in start["authorization_url"] and "state=" in start["authorization_url"]
    state = httpx.URL(start["authorization_url"]).params["state"]
    bad = client.get("/api/oauth/callback/youtube", params={"state": "forged", "code": "x"}, follow_redirects=False)
    assert "invalid_state" in bad.headers["location"]
    ok = client.get("/api/oauth/callback/youtube", params={"state": state, "code": "authcode"}, follow_redirects=False)
    assert "connected" in ok.headers["location"], ok.headers["location"]
    replay = client.get("/api/oauth/callback/youtube", params={"state": state, "code": "authcode"}, follow_redirects=False)
    assert "invalid_state" in replay.headers["location"]  # single use
    dbs.expire_all()
    a = dbs.get(SocialAccount, acc["id"])
    assert a.status == "connected" and a.external_account_id == "UC123"
    listed = client.get("/api/accounts").json()[0]
    assert "ya29" not in json.dumps(listed)  # tokens never exposed
    settings = client.get("/api/settings").json()
    assert "shh-secret" not in json.dumps(settings) and settings["secrets"]["oauth.youtube.client_secret"]["configured"]
    # publish through the real service with the mocked Google endpoints
    p = _ready_publication(dbs, env["tmp"])
    p.social_account_id = a.id
    dbs.commit()
    from app.services.publishing.service import publish_publication

    res = publish_publication(dbs, p.id)
    assert res["status"] == "published" and res["url"].endswith("abc123")


def test_s6_tiktok_chunked_upload_contract():
    from pathlib import Path
    import tempfile

    from app.services.publishing.base import PostContent, PublishContext, TokenSet
    from app.services.publishing.tiktok import MB, TikTokAdapter, chunk_plan

    assert chunk_plan(3 * MB) == (3 * MB, 1)
    assert chunk_plan(50_000_123) == (10 * MB, 4)
    puts = []

    def handler(request):
        u = str(request.url)
        if u.endswith("/creator_info/query/"):
            return httpx.Response(200, json={"data": {"privacy_level_options": ["SELF_ONLY"], "max_video_post_duration_sec": 600},
                                             "error": {"code": "ok"}})
        if u.endswith("/video/init/"):
            body = json.loads(request.content)
            assert body["post_info"]["privacy_level"] == "SELF_ONLY" and body["source_info"]["source"] == "FILE_UPLOAD"
            return httpx.Response(200, json={"data": {"publish_id": "p1", "upload_url": "https://up.example/u"}, "error": {"code": "ok"}})
        if u == "https://up.example/u":
            puts.append(request.headers["Content-Range"])
            return httpx.Response(201)
        if u.endswith("/status/fetch/"):
            return httpx.Response(200, json={"data": {"status": "PUBLISH_COMPLETE"}, "error": {"code": "ok"}})
        return httpx.Response(404)

    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / "v.mp4"
        f.write_bytes(b"\x00" * (12 * MB + 5))
        state = {}
        ctx = PublishContext(f, {"duration": 20}, PostContent("t", "caption", ["#a"]), TokenSet("tok"), {"privacy_level": "SELF_ONLY"},
                             {}, lambda s: state.update(s))
        res = TikTokAdapter(httpx.Client(transport=httpx.MockTransport(handler))).publish(ctx)
    assert res.status == "published" and state["publish_id"] == "p1"
    assert puts == [f"bytes 0-{12 * MB + 4}/{12 * MB + 5}"]  # single chunk: < 2x chunk size absorbed into last chunk


# ---------------------------------------------------------------- Scenario 7
def test_s7_budget_limit_blocks_ai_jobs(client, dbs, fake_claude):
    from app.models import ScheduledJob

    client.patch("/api/settings/ai", json={"budgets": {"daily_calls": 1}})
    a = client.post("/api/applications", json={"name": "TaskFlow", "main_features": ["Sprint board"]}).json()
    run_job(client.post(f"/api/applications/{a['id']}/analyze").json()["job_id"])  # consumes the 1 allowed call
    assert len(fake_claude.requests) == 1
    job_id = client.post("/api/studio/generate", json={"application_id": a["id"], "render": False}).json()["job_id"]
    job = run_job(job_id)
    assert job.status == "blocked" and "daily Claude call limit" in job.blocked_reason
    assert len(fake_claude.requests) == 1  # no call was made
    assert job.attempts == 0  # blocking does not consume retries
    usage = client.get("/api/usage").json()
    assert usage["budget"]["exceeded"] is True
    # cost-saving alternative: local fallback keeps content flowing with zero spend
    client.patch("/api/settings/ai", json={"on_budget_exceeded": "local_fallback"})
    dbs.expire_all()
    j = dbs.get(ScheduledJob, job_id)
    j.status, j.run_at = "queued", datetime.utcnow()
    dbs.commit()
    job = run_job(job_id)
    assert job.status == "succeeded" and len(fake_claude.requests) == 1


# ---------------------------------------------------------------- Scenario 8 (content duplicates)
def test_s8_repeated_topics_are_avoided(dbs, env):
    from app.services.content.planner import plan_content

    a = _mk_app(dbs, "Dedup")
    topics = set()
    for _ in range(3):
        res = plan_content(dbs, a, force_local=True)
        topics.add(res.idea.fingerprint)
    assert len(topics) == 3  # cool-down forces different topics


# ---------------------------------------------------------------- Scenario 10 (dashboard-only workflow)
def test_s10_core_workflow_via_api_only(client, dbs):
    a = client.post("/api/applications", json={"name": "Flowly", "main_features": ["One-click reports", "Team inbox"],
                                                "publishing_mode": "auto", "brand_colors": ["#16A34A", "#0F172A"]}).json()
    client.patch("/api/settings/comfyui", json={"enabled": False})
    client.patch("/api/settings/video", json={"voice": {"enabled": False}})
    acc = client.post("/api/accounts", json={"application_id": a["id"], "platform": "tiktok", "display_name": "Flowly TT",
                                             "export_only": True}).json()
    assert acc["status"] == "export_only"
    rule = client.post("/api/rules", json={"social_account_id": acc["id"], "posts_per_period": 1, "generation_lead_hours": 48,
                                           "posting_times": ["00:00"], "missed_policy": "publish_late", "grace_minutes": 1440}).json()
    prev = client.post("/api/rules/preview", json={"social_account_id": acc["id"], "posts_per_period": 2}).json()
    assert prev["count"] >= 26
    tick = client.post("/api/system/tick").json()
    assert tick["prepare_jobs"] >= 1, tick
    jobs = run_all_jobs()
    assert jobs and all(j.status == "succeeded" for j in jobs), [(j.job_type, j.last_error) for j in jobs]
    pubs = client.get("/api/publications").json()
    approved = [p for p in pubs if p["status"] == "approved"]
    assert approved, pubs
    r = client.post(f"/api/publications/{approved[0]['id']}/publish-now").json()
    assert run_job(r["job_id"]).status == "succeeded"
    pub = client.get(f"/api/publications/{approved[0]['id']}").json()
    assert pub["status"] == "exported" and pub["export_path"]
    from pathlib import Path

    assert (Path(pub["export_path"]) / "video.mp4").exists() and (Path(pub["export_path"]) / "caption.txt").exists()
    ov = client.get("/api/system/overview").json()
    assert ov["stats"]["applications"] == 1
    cal = client.get("/api/calendar", params={"start": "2026-01-01T00:00:00Z", "end": "2026-02-15T00:00:00Z"})
    assert cal.status_code == 200
    assert rule["id"]


def test_csrf_header_required(client):
    r = client.post("/api/applications", json={"name": "x"}, headers={"X-Requested-With": ""})
    assert r.status_code == 403


def test_auth_required(env):
    from fastapi.testclient import TestClient

    from app.main import create_app

    with TestClient(create_app()) as c:
        assert c.get("/api/applications").status_code == 401
        assert c.get("/api/auth/status").json()["setup_required"] is True


@pytest.mark.parametrize("bad", ["../../etc/passwd", "C:/Windows/System32"])
def test_asset_paths_cannot_escape_storage(env, bad):
    from app.core.paths import PathNotAllowed, storage_path

    with pytest.raises(PathNotAllowed):
        storage_path(bad)


def test_import_external_video_conforms_qcs_and_publishes_like_generated(client, env):
    """A landscape, silent clip made by another tool is conformed to 1080x1920 H.264/AAC, passes QC, keeps the
    user's caption, is rejected as a duplicate on re-upload, and can be queued on an account."""
    from app.services.video import ffmpeg

    src = env["tmp"] / "external.mp4"
    ffmpeg.run(["-f", "lavfi", "-i", "testsrc2=size=640x360:rate=25:duration=5", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                str(src)])
    a = client.post("/api/applications", json={"name": "Stylwear"}).json()
    form = {"title": "Packing demo", "caption": "Pack outfits, not possibilities.", "hashtags": "#travel, packing #Stylwear"}
    with open(src, "rb") as fh:
        r = client.post(f"/api/applications/{a['id']}/videos/import", data=form, files={"file": ("demo.mp4", fh, "video/mp4")})
    assert r.status_code == 201, r.text
    v = r.json()
    assert v["status"] == "ready" and v["renderer"] == "imported+conformed"
    assert (v["width"], v["height"]) == (1080, 1920) and abs(v["fps"] - 30) < 1
    assert v["qc_report"]["ok"], v["qc_report"]
    d = client.get(f"/api/videos/{v['id']}").json()
    ig = d["script"]["social_content"]["instagram"]
    assert ig["caption"] == "Pack outfits, not possibilities." and ig["hashtags"] == ["#travel", "#packing", "#Stylwear"]
    assert d["script"]["social_content"]["youtube"]["title"] == "Packing demo"
    # same source again -> identical conformed output -> duplicate
    with open(src, "rb") as fh:
        r2 = client.post(f"/api/applications/{a['id']}/videos/import", data=form, files={"file": ("demo.mp4", fh, "video/mp4")})
    assert r2.status_code == 409
    # not a video
    r3 = client.post(f"/api/applications/{a['id']}/videos/import", data=form, files={"file": ("x.mp4", b"not a video" * 50, "video/mp4")})
    assert r3.status_code == 400
    # queue it on an export account like any generated video
    acc = client.post("/api/accounts", json={"application_id": a["id"], "platform": "instagram", "display_name": "IG",
                                             "export_only": True}).json()
    p = client.post("/api/publications", json={"video_id": v["id"], "social_account_id": acc["id"], "approve": True})
    assert p.status_code == 201, p.text
    assert p.json()["caption"] == "Pack outfits, not possibilities."
