from __future__ import annotations

from datetime import datetime

from app.core.logging_setup import redact
from app.services.content.dedup import jaccard, shingles, topic_fingerprint
from app.services.knowledge.classifier import classify_commit, doc_added_text, latest_changelog_sections
from app.services.scheduling.slots import RuleSpec, compute_slots, distribute_times, weekly_days


def test_distribute_times_even():
    ts = distribute_times(3, "09:00", "21:00")
    assert [t.strftime("%H:%M") for t in ts] == ["11:00", "15:00", "19:00"]


def test_daily_slots_timezone_and_explicit_times():
    spec = RuleSpec(posts_per_period=2, posting_times=["09:00", "18:30"], timezone="America/New_York")
    s = compute_slots(spec, datetime(2026, 7, 1), datetime(2026, 7, 2))
    assert [x.strftime("%H:%M") for x in s] == ["13:00", "22:30"]  # EDT = UTC-4


def test_dst_transition_keeps_local_time():
    spec = RuleSpec(posts_per_period=1, posting_times=["09:00"], timezone="Europe/Berlin")
    s = compute_slots(spec, datetime(2026, 10, 24), datetime(2026, 10, 27))
    assert [x.hour for x in s] == [7, 8, 8]  # CEST (UTC+2) -> CET (UTC+1) on Oct 25


def test_weekly_and_days_and_campaign_and_blackout():
    assert weekly_days(list(range(7)), 3) == [0, 2, 4]
    spec = RuleSpec(frequency="weekly", posts_per_period=3, posting_times=["10:00"])
    s = compute_slots(spec, datetime(2026, 11, 16), datetime(2026, 11, 23))
    assert [x.weekday() for x in s] == [0, 2, 4]
    spec = RuleSpec(posts_per_period=1, posting_times=["10:00"], days_of_week=[5, 6])
    assert all(x.weekday() in (5, 6) for x in compute_slots(spec, datetime(2026, 11, 16), datetime(2026, 11, 30)))
    spec = RuleSpec(posts_per_period=1, posting_times=["10:00"], start_date="2026-11-18", end_date="2026-11-19",
                    blackout_periods=[{"start": "2026-11-19T00:00", "end": "2026-11-20T00:00"}])
    s = compute_slots(spec, datetime(2026, 11, 16), datetime(2026, 11, 30))
    assert [x.day for x in s] == [18]


def test_max_per_day():
    spec = RuleSpec(posts_per_period=5, max_per_day=2)
    assert len(compute_slots(spec, datetime(2026, 11, 16), datetime(2026, 11, 17))) == 2


def test_classifier():
    assert classify_commit("feat(board): add swimlanes")[0] == "feature"
    assert classify_commit("chore(deps): bump react from 18 to 19")[1] < 0.1
    cat, sig, _ = classify_commit("Fix typo in readme")
    assert sig < 0.1
    cat, sig, uf = classify_commit("Add export to PDF", files=[{"path": "src/pages/export.tsx", "added": 50, "deleted": 0}])
    assert cat == "feature" and uf and sig >= 0.6
    assert classify_commit("update", files=[{"path": "tests/test_a.py"}])[0] == "chore"


def test_doc_diff_and_changelog():
    assert doc_added_text("a\nb\n", "a\nb\nc new\n") == "c new"
    secs = latest_changelog_sections("# Changelog\n## [1.2.0] - 2026-11-01\n### Added\n- Dark mode\n## [1.1.0]\n- old\n")
    assert secs[0][0].startswith("[1.2.0]") and "Dark mode" in secs[0][1]


def test_similarity_and_fingerprint():
    a = shingles("TaskFlow lets you plan sprints with a drag and drop board in seconds")
    b = shingles("TaskFlow lets you plan sprints with a drag and drop board in minutes")
    c = shingles("Export beautiful PDF reports for your clients with one click")
    assert jaccard(a, b) > 0.6 > jaccard(a, c)
    assert topic_fingerprint("evergreen", "Sprint board", ["F001"]) == topic_fingerprint("evergreen", "sprint  BOARD!", ["F001"])


def test_redaction():
    s = redact("Authorization: Bearer ya29.abc.def and sk-ant-api03-SECRETSECRET and access_token=xyz123&x=1")
    assert "ya29.abc" not in s and "SECRETSECRET" not in s and "xyz123" not in s


def test_claims_checker_flags_unreleased_and_superlatives():
    from app.services.ai.schemas import ContentPlan
    from app.services.content.claims import check_plan

    facts = [{"id": "F001", "text": "Sprint board", "status": "released"}, {"id": "F002", "text": "Mobile app", "status": "planned"}]
    plan = ContentPlan.model_validate({"topic": "Planning", "hook": "The #1 planning tool", "scenes": [
        {"duration_seconds": 3, "narration": "Now on mobile", "fact_ids": ["F002"]},
        {"duration_seconds": 3, "narration": "ok", "fact_ids": ["F001"]}]})
    rep = check_plan(plan, facts)
    types = {i["type"] for i in rep["issues"]}
    assert not rep["ok"] and {"unreleased_feature", "risky_language"} <= types


def test_ai_output_validation_rejects_bad_scenes():
    import pytest
    from pydantic import ValidationError

    from app.services.ai.schemas import ContentPlan

    with pytest.raises(ValidationError):
        ContentPlan.model_validate({"topic": "x", "scenes": [{"duration_seconds": 500}]})


def test_comfy_workflow_validation_and_mapping(env):
    import json

    import httpx

    from app.services.video.comfyui import ComfyClient

    info = {"KSampler": {"input": {"required": {"seed": ["INT"], "model": ["MODEL"]}}},
            "CLIPTextEncode": {"input": {"required": {"text": ["STRING"], "clip": ["CLIP"]}}},
            "CheckpointLoaderSimple": {"input": {"required": {"ckpt_name": [["sdxl.safetensors"]]}}}}
    c = ComfyClient("http://comfy.test", transport=httpx.MockTransport(lambda r: httpx.Response(200, json=info)))
    wf = {"1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "missing.safetensors"}},
          "2": {"class_type": "CLIPTextEncode", "inputs": {"text": "x", "clip": ["1", 1]}},
          "3": {"class_type": "FancyCustomNode", "inputs": {}}}
    rep = c.validate(wf, {"positive_prompt": {"node": "2", "input": "text"}, "seed": {"node": "9", "input": "seed"}})
    assert not rep["ok"] and rep["missing_nodes"] == ["FancyCustomNode"] and rep["missing_models"]
    assert any("missing node 9" in p for p in rep["problems"])
    applied = ComfyClient.apply_mappings(wf, {"positive_prompt": {"node": "2", "input": "text"}}, {"positive_prompt": "hello"})
    assert applied["2"]["inputs"]["text"] == "hello" and wf["2"]["inputs"]["text"] == "x"
    assert not c.validate({"nodes": [], "links": []}, {})["ok"]
    _ = json


def test_instagram_token_connect_exchanges_for_long_lived_and_finds_ig_account():
    import httpx

    from app.services.publishing.base import OAuthClient
    from app.services.publishing.instagram import InstagramAdapter

    seen = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req.url.path)
        if req.url.path.endswith("/oauth/access_token"):
            assert req.url.params["grant_type"] == "fb_exchange_token"
            assert req.url.params["fb_exchange_token"] == "short-lived-token-from-explorer"
            return httpx.Response(200, json={"access_token": "long-lived", "expires_in": 5183944})
        if req.url.path.endswith("/me/accounts"):
            assert req.url.params["access_token"] == "long-lived"
            return httpx.Response(200, json={"data": [
                {"id": "p1", "name": "Other", "instagram_business_account": {"id": "111", "username": "other"}},
                {"id": "p2", "name": "Stylwear", "instagram_business_account": {"id": "222", "username": "stylwear"}}]})
        return httpx.Response(404)

    ad = InstagramAdapter(httpx.Client(transport=httpx.MockTransport(handler)))
    s = {"variant": "facebook_login", "graph_version": "v25.0", "ig_user_id": "222"}
    tokens = ad.connect_with_token(OAuthClient("app-id", "app-secret"), "short-lived-token-from-explorer", s)
    assert tokens.access_token == "long-lived" and tokens.expires_at is not None
    info = ad.account_info(tokens, s)
    assert info == {"external_account_id": "222", "display_name": "@stylwear"}
    assert tokens.extra["page_id"] == "p2"


def test_instagram_resumes_failed_upload_into_same_container(tmp_path):
    """A TLS/network failure during the rupload step must not strand the post: verify() reports IN_PROGRESS as
    not published, and the retry re-uploads into the same container and publishes exactly once."""
    import httpx

    from app.services.publishing.base import PostContent, PublishContext, TokenSet
    from app.services.publishing.instagram import InstagramAdapter
    from urllib.parse import parse_qs

    video = tmp_path / "v.mp4"
    video.write_bytes(b"\0" * 1000)
    calls = {"create": 0, "upload": 0, "publish": 0}
    fail_upload = {"on": True}

    def handler(req: httpx.Request) -> httpx.Response:
        path = req.url.path
        if path.endswith("/content_publishing_limit"):
            return httpx.Response(200, json={"data": [{"quota_usage": 0, "config": {"quota_total": 100}}]})
        if path.endswith("/ig1/media"):
            calls["create"] += 1
            calls["ai"] = parse_qs(req.content.decode()).get("is_ai_generated")
            return httpx.Response(200, json={"id": "c1", "uri": "https://rupload.facebook.com/ig-api-upload/v25.0/c1"})
        if req.url.host == "rupload.facebook.com":
            if fail_upload["on"]:
                raise httpx.ConnectError("CERTIFICATE_VERIFY_FAILED")
            calls["upload"] += 1
            return httpx.Response(200, json={"success": True})
        if path.endswith("/c1"):
            return httpx.Response(200, json={"status_code": "FINISHED" if calls["upload"] else "IN_PROGRESS"})
        if path.endswith("/media_publish"):
            calls["publish"] += 1
            return httpx.Response(200, json={"id": "m1"})
        if path.endswith("/m1"):
            if req.url.params.get("fields") == "is_ai_generated":
                return httpx.Response(200, json={"is_ai_generated": True})
            return httpx.Response(200, json={"permalink": "https://www.instagram.com/reel/abc/"})
        return httpx.Response(404)

    state: dict = {}

    def save(patch):
        state.update(patch)

    def ctx():
        return PublishContext(video_path=video, video_meta={}, post=PostContent("t", "cap", [], ai_generated_visuals=True),
                              tokens=TokenSet("tok", extra={"ig_user_id": "ig1"}),
                              account_settings={"variant": "facebook_login", "poll_seconds": 0},
                              state=state, save_state=save, progress=lambda m: None, public_url=None)

    ad = InstagramAdapter(httpx.Client(transport=httpx.MockTransport(handler)))
    import pytest

    with pytest.raises(httpx.ConnectError):
        ad.publish(ctx())
    assert state["container_id"] == "c1" and not state["uploaded"]
    assert ad.verify(ctx()).status == "not_published"
    fail_upload["on"] = False
    res = ad.publish(ctx())
    assert res.status == "published" and res.external_id == "m1"
    assert calls == {"create": 1, "upload": 1, "publish": 1, "ai": ["true"]}
    assert state["ai_label"] is True


def test_platform_limits_and_qc_wording():
    from app.services.publishing import limits
    from app.services.publishing.base import PostContent
    from app.services.publishing.instagram import InstagramAdapter
    from app.services.video import qc
    from app.services.video.ffmpeg import ProbeResult

    MB, GB = limits.MB, limits.GB
    # 1 GB, 60 s: Instagram (300 MB) can't take it; TikTok (4 GB) and YouTube can
    e = limits.eligibility(60, 1 * GB)
    assert not e["instagram"]["ok"] and "over the 300 MB limit" in e["instagram"]["problems"][0]
    assert e["tiktok"]["ok"] and e["youtube"]["ok"]
    sev, detail = limits.summary(e)
    assert sev == "notice" and detail.startswith("Will only be published to TikTok, YouTube.")
    assert "Instagram Reels: file 1.0 GB is over the 300 MB limit" in detail
    # 5 min, 80 MB: everywhere, with notes (TikTok account permission, YouTube regular video)
    e = limits.eligibility(300, 80 * MB)
    assert all(v["ok"] for v in e.values())
    assert "5- or 10-minute" in e["tiktok"]["notes"][0] and "not a Short" in e["youtube"]["notes"][0]
    # 20 min: only YouTube (Instagram 15 min, TikTok 10 min), and it needs a verified account
    e = limits.eligibility(20 * 60, 200 * MB)
    assert [p for p, v in e.items() if v["ok"]] == ["youtube"] and "verified" in e["youtube"]["notes"][0]
    # under Instagram's 3 s minimum; and an excessive bitrate
    assert not limits.check_platform("instagram", 2, 1 * MB)["ok"]
    assert "bitrate" in limits.check_platform("instagram", 10, 50 * MB)["problems"][0]  # 40 Mbps average
    # nothing fits -> QC hard error; fits somewhere -> video stays usable
    assert limits.summary(limits.eligibility(13 * 3600, 2 * GB))[0] == "error"
    rep = qc.check_video(ProbeResult(60, 1080, 1920, 25, "h264", "aac", "yuv420p", 1 * GB),
                         {"width": 1080, "height": 1920, "fps": 25}, None, check_duration=False)
    row = next(c for c in rep["checks"] if c["name"] == "platforms")
    assert rep["ok"] and row["ok"] and row["severity"] == "notice" and not rep["platforms"]["instagram"]["ok"]
    # publish-time preflight refuses the platform with the official limit
    probs = InstagramAdapter().preflight({"duration": 600, "size_bytes": 400 * MB, "width": 1080, "height": 1920},
                                         PostContent("t", "c", []), {})
    assert probs == ["Instagram Reels: file 400 MB is over the 300 MB limit"]
