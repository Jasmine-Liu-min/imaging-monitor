"""Unit tests for battlecard aggregation."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from imaging_monitor.cli import _strip_env_comment
from imaging_monitor.models import Candidate
from imaging_monitor.report import build_battlecards
from imaging_monitor.rules import fallback_event
from imaging_monitor.feishu import build_card

KW = {
    "brands": {"DJI": {"aliases": ["DJI"]}, "GoPro": {"aliases": ["GoPro"]}, "Insta360": {"aliases": ["Insta360"]}},
    "categories": {"hardware": {"keywords": ["camera", "相机"]}},
}


def _events():
    cands = [
        Candidate(id="1", title="DJI new camera", url="http://x/1", source_id="s", source_name="S", confidence="official"),
        Candidate(id="2", title="DJI another camera", url="http://x/2", source_id="s", source_name="S", confidence="media"),
        Candidate(id="3", title="GoPro camera", url="http://x/3", source_id="s", source_name="S", confidence="media"),
    ]
    return [fallback_event(c, KW) for c in cands]


def test_battlecards_aggregate_by_brand():
    events = _events()
    cards = build_battlecards(events, KW, new_ids={events[0].id})
    brands = {c["brand"]: c for c in cards}
    assert "DJI" in brands and brands["DJI"]["event_count"] == 2
    assert brands["DJI"]["new_count"] == 1
    assert "GoPro" in brands and brands["GoPro"]["event_count"] == 1
    # Brands with no events this cycle are not carded.
    assert "Insta360" not in brands
    # Every card carries a strategic insight string and a latest-events list.
    assert all(isinstance(c["key_insight_zh"], str) and c["key_insight_zh"] for c in cards)
    assert all(isinstance(c["latest_events"], list) for c in cards)


def test_battlecards_empty():
    assert build_battlecards([], KW, new_ids=set()) == []


def test_feishu_card_sections_prefer_push_events():
    events = _events()
    old_event = events[0].to_dict()
    old_event["region"] = "domestic"
    old_event["category"] = "hardware"
    new_event = events[2].to_dict()
    new_event["region"] = "global"
    new_event["category"] = "hardware"
    report = {
        "generated_at": "2026-01-02T02:30:00+00:00",
        "window": {"start": "2026-01-01T09:30:00+00:00", "end": "2026-01-02T02:30:00+00:00"},
        "summary": {"new_count": 1, "new_pushable_count": 1, "candidate_count": 2, "domestic_count": 1, "global_count": 1},
        "push_events": [new_event],
        "top5": [old_event],
        "sections": {"domestic": [old_event], "global": [old_event]},
        "battlecards": [],
        "quality": {"ai_ok": True, "ai_stage": "extractor+analyst", "events_without_evidence": 0, "events_discarded": 0, "source_failed": 0},
        "health": [],
    }
    card = build_card(report)
    text = "\n".join(el.get("content", "") for el in card["card"]["elements"] if el.get("tag") == "markdown")
    assert new_event["title_zh"] in text
    assert old_event["title_zh"] not in text


def test_strip_env_comment():
    assert _strip_env_comment("6            # comment") == "6"
    assert _strip_env_comment('"6 # keep"') == '"6 # keep"'


def main() -> int:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
