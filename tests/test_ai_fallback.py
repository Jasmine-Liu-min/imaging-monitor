"""AI error classification + fallback behaviour without hitting the network."""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from imaging_monitor.ai import _classify_error, _event_from_ai, _normalize_brand, extract_events
from imaging_monitor.models import Candidate
from imaging_monitor.util import is_retryable_error

KW = {"brands": {"DJI": {"aliases": ["DJI"]}}, "categories": {}}


def test_classify_bad_json():
    try:
        json.loads("{not json")
    except json.JSONDecodeError as exc:
        assert _classify_error(exc) == "bad_json"


def test_classify_network():
    assert _classify_error(RuntimeError("HTTP 503: service unavailable")) == "network"
    assert _classify_error(TimeoutError("timed out")) == "network"


def test_classify_other():
    assert _classify_error(ValueError("bad config")) == "other"


def test_normalize_brand():
    kw = {"brands": {"DJI": {"aliases": ["DJI", "大疆"]}, "Insta360": {"aliases": ["Insta360", "影石"]}}}
    assert _normalize_brand("影石", kw) == "Insta360"
    assert _normalize_brand("大疆", kw) == "DJI"
    assert _normalize_brand("大疆/影石", kw) == "DJI"   # 取首个命中的品牌
    assert _normalize_brand("Adobe", kw) == "Adobe"      # 未知但非空品牌原样保留
    assert _normalize_brand("", kw) == "Other"
    assert _normalize_brand(None, kw) == "Other"


def test_evidence_falls_back_to_url():
    c = Candidate(id="1", title="DJI Osmo Pocket 4P", url="http://x/p4p", source_id="s", source_name="DJI News", confidence="media")
    # AI 把 evidence 返回成字符串数组（非对象）—— 清洗后应回退到候选 url，而非变空
    ev = _event_from_ai(c, {"candidate_id": "1", "evidence": ["http://x/p4p"]}, KW)
    assert ev.evidence and ev.evidence[0]["url"] == "http://x/p4p", ev.evidence
    # AI 完全不给 evidence 时也回退
    ev2 = _event_from_ai(c, {"candidate_id": "1"}, KW)
    assert ev2.evidence and ev2.evidence[0]["url"] == "http://x/p4p"


def test_is_retryable():
    assert is_retryable_error(RuntimeError("429 too many requests"))
    assert not is_retryable_error(ValueError("totally fatal"))


def test_extract_without_key_uses_rules(monkeypatch_env=None):
    # No provider key -> rule fallback, AI marked disabled, pipeline never raises.
    saved = {k: os.environ.get(k) for k in ("AI_PROVIDER", "AI_API_KEY", "ANTHROPIC_API_KEY", "CLAUDE_API_KEY")}
    try:
        os.environ["AI_PROVIDER"] = "openai-compatible"
        for k in ("AI_API_KEY", "ANTHROPIC_API_KEY", "CLAUDE_API_KEY"):
            os.environ.pop(k, None)
        cands = [Candidate(id="1", title="DJI camera", url="http://x/1", source_id="s", source_name="S", confidence="media")]
        events, status = extract_events(cands, KW)
        assert status["enabled"] is False
        assert status["ok"] is True
        assert len(events) == 1
    finally:
        for k, val in saved.items():
            if val is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = val


def main() -> int:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
