from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

from .models import MarketEvent

STORE_FILENAME = "store.json"
MAX_RUNS = 120  # keep ~2 months of history at 2 runs/day
MAX_EVENT_AGE_DAYS = 60  # drop events not seen for this long to bound file size
MAX_HISTORY_PER_EVENT = 10


def store_path(out_dir: str) -> str:
    return os.path.join(out_dir, STORE_FILENAME)


def load_store(out_dir: str) -> Dict:
    """Load the cross-run event store, returning an empty skeleton if absent/corrupt."""
    path = store_path(out_dir)
    if not os.path.exists(path):
        return {"events": {}, "runs": []}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return {"events": {}, "runs": []}
    if not isinstance(data, dict):
        return {"events": {}, "runs": []}
    data.setdefault("events", {})
    data.setdefault("runs", [])
    return data


def update_store(store: Dict, events: List[MarketEvent], generated_at: str) -> Tuple[Dict, List[str], List[str]]:
    """Reconcile this run's events against the store.

    Mutates each event's ``first_seen``/``is_new`` in place and returns
    ``(store, new_ids, updated_ids)``. Dedup key uses a stable source identity
    so AI title rewriting does not re-create the same story on later runs.
    """
    events_store: Dict = store.setdefault("events", {})
    new_ids: List[str] = []
    updated_ids: List[str] = []

    for e in events:
        fp = _event_fingerprint(e)
        key = stable_event_key(e)
        record = events_store.get(key)
        if record is None:
            e.first_seen = generated_at
            e.is_new = True
            new_ids.append(e.id)
            events_store[key] = {
                "event_id": e.id,
                "first_seen": generated_at,
                "last_seen": generated_at,
                "brand": e.brand,
                "title_zh": e.title_zh or e.title,
                "fingerprint": fp,
                "history": [{"at": generated_at, **fp}],
            }
        else:
            e.first_seen = record.get("first_seen", generated_at)
            e.is_new = False
            record["event_id"] = e.id
            record["last_seen"] = generated_at
            if record.get("fingerprint") != fp:
                updated_ids.append(e.id)
                record["fingerprint"] = fp
                record["history"] = (record.get("history", []) + [{"at": generated_at, **fp}])[-MAX_HISTORY_PER_EVENT:]

    return store, new_ids, updated_ids


def stable_event_key(event: MarketEvent) -> str:
    """Cross-run identity key for the same real-world story.

    Prefer upstream source identity + URL so AI wording changes do not make the
    same article look "new" on the next run. Fall back to a normalized title
    signature only when the source URL is missing.
    """
    source_url = (event.source_url or "").strip().lower()
    if source_url:
        return f"{event.source_id}|{source_url}"
    title = (event.title_original or event.title_zh or event.title or "").strip().lower()
    published = (event.published_at or "")[:10]
    return f"{event.source_id}|{published}|{title[:120]}"


def append_run(store: Dict, summary: Dict) -> Dict:
    """Append a per-run summary used to drive the dashboard trend line."""
    runs: List = store.setdefault("runs", [])
    runs.append(summary)
    if len(runs) > MAX_RUNS:
        store["runs"] = runs[-MAX_RUNS:]
    return store


def prune_store(store: Dict, now_iso: str = "") -> Dict:
    """Drop events not seen within MAX_EVENT_AGE_DAYS to keep the file small."""
    now = _parse_iso(now_iso) or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=MAX_EVENT_AGE_DAYS)
    kept: Dict = {}
    for eid, rec in store.get("events", {}).items():
        last = _parse_iso(rec.get("last_seen", rec.get("first_seen", "")))
        if last is None or last >= cutoff:
            kept[eid] = rec
    store["events"] = kept
    return store


def save_store(store: Dict, out_dir: str) -> None:
    os.makedirs(out_dir, exist_ok=True)
    with open(store_path(out_dir), "w", encoding="utf-8") as f:
        json.dump(store, f, ensure_ascii=False, indent=2)


def build_trend(store: Dict, limit: int = 14) -> List[Dict]:
    """Return the most recent run summaries for the dashboard trend chart."""
    return list(store.get("runs", []))[-limit:]


def _event_fingerprint(e: MarketEvent) -> Dict:
    return {
        "importance_score": e.importance_score,
        "threat_level": e.threat_level,
        "push_decision": e.push_decision,
        "evidence_count": len(e.evidence or []),
    }


def _parse_iso(value: str) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt
