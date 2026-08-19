from __future__ import annotations

import json
import os
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from .models import MarketEvent
from .rules import make_basic_insight, should_push
from .util import utc_now_iso, week_tag


CATEGORY_LABELS = {
    "hardware": "硬件动态",
    "software": "配套软件",
    "marketing": "竞品营销",
    "industry": "行业资讯",
    "pmf": "场景/需求信号（PMF）",
    "community": "用户/社区",
}


def build_report(
    events: List[MarketEvent],
    health: List[Dict],
    ai_status: Dict,
    window_hours: int = 17,
    new_ids: Optional[List[str]] = None,
    updated_ids: Optional[List[str]] = None,
    trend: Optional[List[Dict]] = None,
    keywords: Optional[Dict] = None,
) -> Dict:
    new_set = set(new_ids or [])
    updated_set = set(updated_ids or [])
    for e in events:
        e.pushed = should_push(e)
    sorted_events = sorted(events, key=lambda e: (_threat_rank(e.threat_level), e.importance_score, e.confidence_score, e.source_weight), reverse=True)
    now = datetime.now(timezone.utc)
    start = now - timedelta(hours=window_hours)
    category_counts = Counter(e.category for e in events)
    brand_counts = Counter(e.brand for e in events)
    region_counts = Counter(e.region for e in events)
    language_counts = Counter(e.language for e in events)
    decision_counts = Counter(e.push_decision for e in events)
    pushable = [e for e in sorted_events if e.pushed]
    top5 = select_top_events(pushable, sorted_events, limit=5)
    sections = build_sections(sorted_events)
    quality = build_quality_report(events, health, ai_status, top5)
    # Only genuinely new (or materially updated) pushable events get pushed,
    # so the same story is never broadcast to Feishu twice.
    new_pushable = [e for e in pushable if e.id in new_set or e.id in updated_set]
    push_events = select_top_events(new_pushable, new_pushable, limit=5)
    battlecards = build_battlecards(sorted_events, keywords or {}, new_set)
    return {
        "title": "影像行业与竞品信息监控报告",
        "generated_at": utc_now_iso(),
        "window": {
            "start": start.isoformat(),
            "end": now.isoformat(),
            "hours": window_hours,
        },
        "week_tag": week_tag(),
        "summary": {
            "candidate_count": len(events),
            "pushable_count": len(pushable),
            "top_count": len(top5),
            "new_count": len(new_set),
            "updated_count": len(updated_set),
            "new_pushable_count": len(new_pushable),
            "source_ok": sum(1 for h in health if h.get("ok")),
            "source_failed": sum(1 for h in health if not h.get("ok")),
            "domestic_count": region_counts.get("domestic", 0),
            "global_count": region_counts.get("global", 0),
        },
        "category_counts": dict(category_counts),
        "brand_counts": dict(brand_counts),
        "region_counts": dict(region_counts),
        "language_counts": dict(language_counts),
        "decision_counts": dict(decision_counts),
        "top5": [e.to_dict() for e in top5],
        "push_events": [e.to_dict() for e in push_events],
        "pushable_events": [e.to_dict() for e in pushable[:12]],
        "sections": {k: [e.to_dict() for e in v] for k, v in sections.items()},
        "battlecards": battlecards,
        "trend": list(trend or []),
        "health": health,
        "ai": ai_status,
        "quality": quality,
        "category_labels": CATEGORY_LABELS,
    }


def build_battlecards(events: List[MarketEvent], keywords: Dict, new_ids: Optional[set] = None) -> List[Dict]:
    """Aggregate events per tracked competitor into Klue-style battlecards.

    One card per brand declared in keywords.yaml (Insta360 included as the
    own-brand view). Brands with no events this cycle are skipped.
    """
    new_ids = new_ids or set()
    brands = list((keywords.get("brands") or {}).keys())
    cards: List[Dict] = []
    for brand in brands:
        brand_events = [e for e in events if e.brand == brand]
        if not brand_events:
            continue
        latest = sorted(brand_events, key=lambda e: (e.published_at or "", _threat_rank(e.threat_level)), reverse=True)
        top_threat = max(brand_events, key=lambda e: (_threat_rank(e.threat_level), e.importance_score))
        new_count = sum(1 for e in brand_events if e.id in new_ids)
        cards.append({
            "brand": brand,
            "is_self": bool((keywords.get("brands") or {}).get(brand, {}).get("self")),
            "event_count": len(brand_events),
            "new_count": new_count,
            "threat_summary": top_threat.threat_level,
            "max_importance": max(e.importance_score for e in brand_events),
            "category_mix": dict(Counter(e.category for e in brand_events)),
            "key_insight_zh": _battlecard_insight(brand, brand_events, top_threat),
            "latest_events": [e.to_dict() for e in latest[:5]],
        })
    # Most active / most threatening competitors first.
    cards.sort(key=lambda c: (_threat_rank(c["threat_summary"]), c["new_count"], c["event_count"]), reverse=True)
    return cards


def _battlecard_insight(brand: str, brand_events: List[MarketEvent], top_threat: MarketEvent) -> str:
    insight = (top_threat.insight_zh or top_threat.insight or "").strip()
    if insight:
        return insight
    dominant_category = Counter(e.category for e in brand_events).most_common(1)[0][0]
    return make_basic_insight(brand, dominant_category, top_threat.title_original or top_threat.title)


def build_sections(events: List[MarketEvent]) -> Dict[str, List[MarketEvent]]:
    push_or_dash = [e for e in events if e.push_decision != "discard"]
    return {
        "domestic": [e for e in push_or_dash if e.region == "domestic" and e.category not in ("software", "community")][:6],
        "global": [e for e in push_or_dash if e.region == "global" and e.category not in ("software", "community")][:6],
        "software": [e for e in push_or_dash if e.category == "software"][:6],
        "community": [e for e in push_or_dash if e.category == "community" or e.confidence == "community"][:6],
        "pmf": [e for e in push_or_dash if e.category == "pmf"][:6],
    }


def select_top_events(primary: List[MarketEvent], fallback: List[MarketEvent], limit: int = 5) -> List[MarketEvent]:
    events = primary or fallback
    selected: List[MarketEvent] = []
    for region in ("domestic", "global"):
        item = next((e for e in events if e.region == region), None) or next((e for e in fallback if e.region == region), None)
        if item and item not in selected:
            selected.append(item)
    for e in events:
        if e not in selected:
            selected.append(e)
        if len(selected) >= limit:
            break
    return selected[:limit]


def build_quality_report(events: List[MarketEvent], health: List[Dict], ai_status: Dict, top5: List[MarketEvent]) -> Dict:
    no_evidence = [e for e in events if not any(ev.get("url") for ev in e.evidence)]
    discarded = [e for e in events if e.push_decision == "discard"]
    pushed = [e for e in events if e.pushed]
    return {
        "generated_at": utc_now_iso(),
        "source_ok": sum(1 for h in health if h.get("ok")),
        "source_failed": sum(1 for h in health if not h.get("ok")),
        "ai_enabled": bool(ai_status.get("enabled")),
        "ai_ok": bool(ai_status.get("ok")),
        "ai_stage": ai_status.get("stage", ""),
        "ai_fallback_count": ai_status.get("fallback_count", 0),
        "events_total": len(events),
        "events_pushed": len(pushed),
        "events_discarded": len(discarded),
        "events_without_evidence": len(no_evidence),
        "top5_source_mix": dict(Counter(e.source_name for e in top5)),
        "top5_region_mix": dict(Counter(e.region for e in top5)),
    }


def write_outputs(events: List[MarketEvent], report: Dict, out_dir: str) -> None:
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "events.json"), "w", encoding="utf-8") as f:
        json.dump([e.to_dict() for e in events], f, ensure_ascii=False, indent=2)
    with open(os.path.join(out_dir, "report.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    with open(os.path.join(out_dir, "quality_report.json"), "w", encoding="utf-8") as f:
        json.dump(report.get("quality", {}), f, ensure_ascii=False, indent=2)


def _threat_rank(value: str) -> int:
    return {"high": 3, "medium": 2, "low": 1}.get(value, 0)
