"""Unit tests for rule-based detection and push gating."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from imaging_monitor.models import Candidate
from imaging_monitor.rules import detect_brand, detect_category, fallback_event, should_push

KW = {
    "brands": {"DJI": {"aliases": ["DJI", "大疆"]}, "GoPro": {"aliases": ["GoPro"]}},
    "categories": {
        "hardware": {"keywords": ["camera", "lens", "相机", "镜头"]},
        "software": {"keywords": ["firmware", "app", "固件", "软件", "version"]},
    },
}


def test_detect_brand():
    assert detect_brand("大疆 Osmo 发布", KW) == "DJI"
    assert detect_brand("GoPro Hero 13", KW) == "GoPro"
    assert detect_brand("Some unrelated headline", KW) == "Other"


def test_detect_category():
    assert detect_category("New camera lens announced", KW) == "hardware"
    assert detect_category("App firmware version update", KW) == "software"
    assert detect_category("纯行业评论没有关键词", KW) == "industry"


def test_should_push_requires_evidence():
    c = Candidate(id="n", title="No evidence story", url="", source_id="s", source_name="S", confidence="media")
    e = fallback_event(c, KW)  # no url -> no evidence
    assert e.evidence == []
    assert should_push(e) is False
    assert e.push_decision == "dashboard_only"


def test_should_push_discard():
    c = Candidate(id="d", title="DJI 大疆 official 相机", url="http://x/1", source_id="s", source_name="S", confidence="official")
    e = fallback_event(c, KW)
    e.push_decision = "discard"
    assert should_push(e) is False


def main() -> int:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
