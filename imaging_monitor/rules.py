from __future__ import annotations

from collections import OrderedDict
from typing import Dict, List, Tuple

from .models import Candidate, MarketEvent
from .util import contains_any, detect_language, keyword_in_text, stable_id, week_tag, zh_title_from_original


def filter_candidates(candidates: List[Candidate], keywords: Dict, limit: int = 60) -> List[Candidate]:
    out: List[Candidate] = []
    excludes = keywords.get("exclude_keywords", [])
    for c in candidates:
        if contains_any(f"{c.title} {c.summary} {c.content}", excludes):
            continue
        # 只保留影像相关：官方信源（均为影像品牌/App）直接信任；其余必须命中影像信号。
        # 相关性只看“标题+摘要”，不看正文——正文太长易因偶然词误判（综合源噪音的主因）。
        if c.confidence == "official" or is_imaging_relevant(f"{c.title} {c.summary}", keywords):
            out.append(c)
    return dedup_candidates(out)[:limit]


def is_imaging_relevant(text: str, keywords: Dict) -> bool:
    """影像领域锚点词命中即相关；否则需命中“影像为主”品牌（imaging_first）。

    综合厂商（OPPO/vivo 等）只有在同时出现影像锚点词时才算相关，避免其耳机/手机
    等非影像产品靠品牌名混入。
    """
    if contains_any(text, keywords.get("imaging_keywords", [])):
        return True
    brand = detect_brand(text, keywords)
    if brand == "Other":
        return False
    return bool(keywords.get("brands", {}).get(brand, {}).get("imaging_first"))


def dedup_candidates(candidates: List[Candidate]) -> List[Candidate]:
    deduped: OrderedDict[str, Candidate] = OrderedDict()
    for c in candidates:
        norm_title = " ".join(c.title.lower().split())[:90]
        key = stable_id(c.url or norm_title, norm_title)
        if key not in deduped:
            deduped[key] = c
    return list(deduped.values())


def merge_events(events: List[MarketEvent]) -> List[MarketEvent]:
    merged: OrderedDict[str, MarketEvent] = OrderedDict()
    for e in events:
        key = stable_id(e.brand, e.title_original[:80], e.published_at[:10])
        existing = merged.get(key)
        if not existing:
            merged[key] = e
            continue
        existing.evidence = _merge_evidence(existing.evidence, e.evidence)
        existing.source_weight = max(existing.source_weight, e.source_weight)
        existing.importance_score = max(existing.importance_score, e.importance_score)
        existing.confidence_score = max(existing.confidence_score, e.confidence_score)
        if _source_rank(e.confidence) > _source_rank(existing.confidence):
            existing.source_name = e.source_name
            existing.source_url = e.source_url
            existing.confidence = e.confidence
    return list(merged.values())


def fallback_event(candidate: Candidate, keywords: Dict) -> MarketEvent:
    text = f"{candidate.title} {candidate.summary} {candidate.content}"
    title_brand = detect_brand(candidate.title, keywords)
    brand = title_brand if title_brand != "Other" else detect_brand(text, keywords)
    category = detect_category(text, keywords)
    threat = "medium" if candidate.confidence == "official" or (brand != "Other" and category in ("hardware", "software")) else "low"
    heat = 4 if candidate.confidence == "official" else 3 if candidate.confidence == "media" else 1
    summary = candidate.summary or candidate.title
    details = [s for s in _sentences(summary)[:3]] or [candidate.title]
    language = candidate.language or detect_language(text, "en")
    title_zh = zh_title_from_original(candidate.title, brand, category, detect_event_type(text, category))
    summary_zh = summary if language == "zh" else f"来源摘要：{summary[:150]}"
    evidence = [{
        "source_name": candidate.source_name,
        "url": candidate.url,
        "snippet": (candidate.summary or candidate.title)[:220],
    }] if candidate.url else []
    importance = _importance_score(candidate, brand, category, threat)
    confidence_score = _confidence_score(candidate.confidence, candidate.weight, bool(evidence))
    event = MarketEvent(
        id=stable_id(candidate.source_id, candidate.title, candidate.url),
        title=title_zh,
        brand=brand,
        category=category,
        event_type=detect_event_type(text, category),
        summary=summary_zh[:220],
        details=details,
        insight=make_basic_insight(brand, category, text),
        threat_level=threat,
        heat_score=heat,
        confidence=candidate.confidence,
        published_at=candidate.published_at,
        source_name=candidate.source_name,
        source_url=candidate.url,
        week_tag=week_tag(candidate.published_at),
        source_id=candidate.source_id,
        ai_confidence=0.0,
        title_zh=title_zh,
        title_original=candidate.title,
        language=language,
        region=candidate.region,
        source_market=candidate.source_market,
        summary_zh=summary_zh[:240],
        details_zh=details,
        insight_zh=make_basic_insight(brand, category, text),
        importance_score=importance,
        confidence_score=confidence_score,
        push_decision="dashboard_only",
        evidence=evidence,
        source_weight=candidate.weight,
    )
    event.raw_title_brand = title_brand  # dynamic hint used only before serialization
    return event


def detect_brand(text: str, keywords: Dict) -> str:
    for brand, data in keywords.get("brands", {}).items():
        if any(keyword_in_text(text, alias) for alias in data.get("aliases", [])):
            return brand
    return "Other"


def detect_category(text: str, keywords: Dict) -> str:
    scores: List[Tuple[str, int]] = []
    lowered = text.lower()
    for cat, data in keywords.get("categories", {}).items():
        score = sum(1 for kw in data.get("keywords", []) if kw.lower() in lowered)
        scores.append((cat, score))
    scores.sort(key=lambda x: x[1], reverse=True)
    category = scores[0][0] if scores and scores[0][1] > 0 else "industry"
    lowered = text.lower()
    if category == "software":
        software_terms = ("app store", "firmware", "software", "version", "dji fly", "dji mimo", "quik", "insta360 app", "固件", "软件", "版本")
        if not any(term in lowered for term in software_terms):
            return "hardware" if any(term in lowered for term in ("camera", "lens", "sensor", "case", "相机", "镜头", "传感器")) else "industry"
    return category


def detect_event_type(text: str, category: str) -> str:
    lowered = text.lower()
    if any(k in lowered for k in ("launch", "announces", "released", "发布", "开售")):
        return "新品发布"
    if any(k in lowered for k in ("update", "firmware", "version", "更新", "版本")):
        return "软件更新"
    if any(k in lowered for k in ("review", "hands-on", "评测")):
        return "评测反馈"
    if any(k in lowered for k in ("deal", "discount", "sale", "促销")):
        return "促销"
    if category == "pmf":
        return "场景信号"
    if category == "community":
        return "社区反馈"
    return "行业趋势"


def make_basic_insight(brand: str, category: str, text: str) -> str:
    if brand == "Insta360":
        return "自有品牌动态，建议同步关注用户反馈、渠道节奏与配套软件承接。"
    if category == "hardware":
        return f"{brand} 硬件动态可能影响同价位产品定位、发布节奏和对比话术。"
    if category == "software":
        return f"{brand} 软件/固件更新会影响创作者工作流体验，建议关注功能点与稳定性反馈。"
    if category == "marketing":
        return f"{brand} 渠道或营销动作可能改变短期转化预期，建议跟踪价格、赠品和区域节奏。"
    return "该动态可作为影像行业趋势输入，建议结合产品路线、内容场景和渠道反馈继续观察。"


def should_push(event: MarketEvent) -> bool:
    if event.push_decision == "discard":
        return False
    if not any(ev.get("url") for ev in event.evidence):
        event.push_decision = "dashboard_only"
        return False
    if event.push_decision == "push":
        return True
    title_brand = getattr(event, "raw_title_brand", event.brand)
    if event.ai_confidence == 0.0 and event.brand == "Other" and event.confidence != "official":
        event.push_decision = "dashboard_only"
        return event.threat_level == "high"
    if event.ai_confidence == 0.0 and event.confidence in ("media", "community") and title_brand == "Other":
        event.push_decision = "dashboard_only"
        return False
    if event.confidence == "rumor":
        ok = event.threat_level in ("high", "medium") and event.ai_confidence >= 0.75
        event.push_decision = "push" if ok else "dashboard_only"
        return ok
    if event.confidence == "community":
        ok = event.importance_score >= 4 and event.confidence_score >= 0.75
        event.push_decision = "push" if ok else "dashboard_only"
        return ok
    ok = event.threat_level in ("high", "medium") and (event.brand != "Other" or event.category == "industry")
    event.push_decision = "push" if ok else "dashboard_only"
    return ok


def _sentences(text: str) -> List[str]:
    return [s.strip() for s in text.replace("。", ".").split(".") if s.strip()]


def _importance_score(candidate: Candidate, brand: str, category: str, threat: str) -> int:
    score = min(5, max(1, int(candidate.weight or 1)))
    if candidate.confidence == "official":
        score += 1
    if brand != "Other":
        score += 1
    if category in ("hardware", "software", "pmf"):
        score += 1
    if threat == "high":
        score += 1
    return max(1, min(5, score))


def _confidence_score(confidence: str, weight: int, has_evidence: bool) -> float:
    base = {"official": 0.9, "media": 0.72, "community": 0.45, "rumor": 0.25}.get(confidence, 0.5)
    base += min(0.08, max(0, weight - 3) * 0.03)
    if not has_evidence:
        base -= 0.3
    return round(max(0.0, min(1.0, base)), 2)


def _merge_evidence(a: List[Dict], b: List[Dict]) -> List[Dict]:
    out: OrderedDict[str, Dict] = OrderedDict()
    for item in a + b:
        url = item.get("url")
        if url and url not in out:
            out[url] = item
    return list(out.values())[:5]


def _source_rank(confidence: str) -> int:
    return {"official": 4, "media": 3, "community": 2, "rumor": 1}.get(confidence, 0)
