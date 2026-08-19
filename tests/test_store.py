"""Cross-run dedup regression — the core guarantee that we never re-push a story."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from imaging_monitor.models import Candidate
from imaging_monitor.rules import fallback_event
from imaging_monitor.store import append_run, build_trend, prune_store, stable_event_key, update_store

KW = {"brands": {"DJI": {"aliases": ["DJI"]}, "GoPro": {"aliases": ["GoPro"]}}, "categories": {}}


def _events():
    cands = [
        Candidate(id="x", title="DJI Osmo 5 launch", url="http://x/1", source_id="s", source_name="S", confidence="media"),
        Candidate(id="y", title="GoPro Hero firmware update", url="http://x/2", source_id="s", source_name="S", confidence="media"),
    ]
    return [fallback_event(c, KW) for c in cands]


def test_first_run_all_new():
    store = {"events": {}, "runs": []}
    events = _events()
    store, new_ids, updated_ids = update_store(store, events, "2026-01-01T00:00:00+00:00")
    assert len(new_ids) == 2, new_ids
    assert updated_ids == []
    assert all(e.is_new and e.first_seen == "2026-01-01T00:00:00+00:00" for e in events)


def test_second_run_no_new():
    store = {"events": {}, "runs": []}
    update_store(store, _events(), "2026-01-01T00:00:00+00:00")
    events2 = _events()
    store, new_ids, updated_ids = update_store(store, events2, "2026-01-02T00:00:00+00:00")
    assert new_ids == [], new_ids
    assert updated_ids == []
    assert all(not e.is_new for e in events2)
    # first_seen is preserved from the original sighting
    assert all(e.first_seen == "2026-01-01T00:00:00+00:00" for e in events2)


def test_fingerprint_change_marks_updated():
    store = {"events": {}, "runs": []}
    update_store(store, _events(), "2026-01-01T00:00:00+00:00")
    events2 = _events()
    events2[0].importance_score = 5  # change a fingerprinted field
    store, new_ids, updated_ids = update_store(store, events2, "2026-01-02T00:00:00+00:00")
    assert new_ids == []
    assert len(updated_ids) == 1


def test_prune_drops_stale_events():
    store = {"events": {}, "runs": []}
    update_store(store, _events(), "2020-01-01T00:00:00+00:00")
    assert len(store["events"]) == 2
    prune_store(store, "2026-01-01T00:00:00+00:00")
    assert store["events"] == {}


def test_run_history_and_trend():
    store = {"events": {}, "runs": []}
    append_run(store, {"generated_at": "2026-01-01T00:00:00+00:00", "candidate_count": 2, "new_count": 2})
    append_run(store, {"generated_at": "2026-01-02T00:00:00+00:00", "candidate_count": 3, "new_count": 1})
    trend = build_trend(store, limit=14)
    assert len(trend) == 2
    assert trend[-1]["new_count"] == 1


def test_stable_event_key_ignores_ai_title_rewrite():
    event = _events()[0]
    base_key = stable_event_key(event)
    event.title = "DJI｜新品发布：DJI Osmo 5 launch"
    event.title_zh = "DJI Osmo 5 正式发布"
    event.title_original = "DJI Osmo 5 launch hands-on"
    assert stable_event_key(event) == base_key


def main() -> int:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
