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
