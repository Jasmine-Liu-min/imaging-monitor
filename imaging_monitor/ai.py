from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List

from .constants import (
    AI_CONTENT_LEN,
    DETAIL_LEN,
    INSIGHT_LEN,
    SUMMARY_LEN,
    TITLE_LEN,
    TITLE_ORIGINAL_LEN,
)
from .log import get_logger
from .models import Candidate, MarketEvent
from .rules import detect_brand, fallback_event, merge_events
from .util import is_retryable_error, stable_id, week_tag

log = get_logger(__name__)


EXTRACTOR_PROMPT = """你是影像行业竞品监控的结构化抽取器。
请把候选资讯抽取为中文可读、带证据链的 JSON。只输出 JSON 对象，格式：
{"events":[...]}
每个 event 必须包含：
candidate_id, title_zh, title_original, language, region, source_market, brand, category,
event_type, summary_zh, details_zh, insight_zh, threat_level, importance_score,
confidence, confidence_score, push_decision, evidence。
约束：
- category: hardware/software/marketing/industry/pmf/community
- threat_level: high/medium/low
- confidence: official/media/community/rumor
- push_decision: push/dashboard_only/discard
- evidence 至少保留候选来源 url 和 source_name；没有证据则 dashboard_only
- 所有说明字段用中文；title_original 保留原文
- 社区来源默认 dashboard_only，除非明确高风险
"""


ANALYST_PROMPT = """你是 Insta360 产品运营部的竞品分析师。
请审阅已抽取事件，修正 importance_score、confidence_score、threat_level、push_decision 和 insight_zh。
规则：
- 没有 evidence.url 的事件必须 dashboard_only
- rumor 默认 dashboard_only
- community 默认 dashboard_only，除非 importance_score >= 4 且 confidence_score >= 0.75
- brand 为 Other 且不是行业关键趋势，不能 push
- 输出 {"events":[...]}，保留原字段。
"""


def extract_events(candidates: List[Candidate], keywords: Dict) -> tuple[List[MarketEvent], Dict]:
    """分片抽取：把候选切成小批，逐片调用 AI。

    每片只产出短 JSON（网关对长输出会截断 → 坏 JSON），所以分片后大部分片稳定成功；
    某一片失败（超时/坏 JSON）只降级该片为规则抽取，不拖垮整批。这样可在保证全量覆盖的
    同时显著提升稳定性。批大小可用 AI_CHUNK_SIZE 调整。
    """
    provider = os.getenv("AI_PROVIDER", "openai-compatible").strip() or "openai-compatible"
    api_key = _provider_api_key(provider)
    if not api_key:
        fallback = [fallback_event(c, keywords) for c in candidates]
        return merge_events(fallback), {"enabled": False, "ok": True, "provider": provider, "fallback_count": len(candidates), "stage": "fallback"}

    chunk_size = max(1, int(os.getenv("AI_CHUNK_SIZE", "6")))
    chunks = [candidates[i:i + chunk_size] for i in range(0, len(candidates), chunk_size)]
    all_events: List[MarketEvent] = []
    stages: set = set()
    ok_chunks = failed_chunks = fallback_count = 0
    last_error: Exception | None = None

    # 分片并发：默认保守，优先兼容不稳定或限流严格的网关；需要提速时再显式调高。
    max_workers = max(1, min(len(chunks), int(os.getenv("AI_MAX_PARALLEL", "2"))))
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_extract_chunk, chunk, keywords, api_key, provider): chunk for chunk in chunks}
        for fut in as_completed(futures):
            chunk = futures[fut]
            try:
                chunk_events, stage = fut.result()
                all_events.extend(chunk_events)
                stages.add(stage)
                ok_chunks += 1
            except Exception as exc:
                last_error = exc
                log.warning("AI chunk failed (%s: %s); rule fallback for this chunk", _classify_error(exc), exc)
                for c in chunk:
                    e = fallback_event(c, keywords)
                    e.push_decision = "dashboard_only"
                    all_events.append(e)
                    fallback_count += 1
                failed_chunks += 1

    status = {
        "enabled": True,
        "ok": ok_chunks > 0,
        "provider": provider,
        "chunks": len(chunks),
        "ok_chunks": ok_chunks,
        "failed_chunks": failed_chunks,
        "fallback_count": fallback_count,
        "stage": "+".join(sorted(stages)) if stages else "fallback_after_ai_error",
    }
    if failed_chunks and last_error is not None:
        status["error_type"] = _classify_error(last_error)
        status["error"] = str(last_error)
    return merge_events(all_events), status


def _extract_chunk(chunk: List[Candidate], keywords: Dict, api_key: str, provider: str) -> tuple[List[MarketEvent], str]:
    """对单个小批跑抽取器 + 分析师；分析师失败则保留抽取结果。抽取器失败则抛出（由上层降级）。"""
    extracted = call_stage(EXTRACTOR_PROMPT, {"items": [_candidate_payload(c) for c in chunk]}, api_key, provider)
    extracted_events = extracted.get("events", []) if isinstance(extracted, dict) else []
    try:
        analyzed = call_stage(ANALYST_PROMPT, {"events": extracted_events}, api_key, provider)
        analyzed_events = analyzed.get("events", extracted_events) if isinstance(analyzed, dict) else extracted_events
        stage = "extractor+analyst"
    except Exception as exc:
        log.warning("analyst stage failed (%s); using extractor output", exc)
        analyzed_events = extracted_events
        stage = "extractor_only"
    return _events_from_ai(chunk, analyzed_events, keywords), stage


def call_stage(prompt: str, payload_obj: Dict, api_key: str, provider: str) -> Dict:
    attempts = max(1, int(os.getenv("AI_RETRY_ATTEMPTS", "3")))
    last_error: Exception | None = None
    for idx in range(attempts):
        try:
            if provider.lower() in ("anthropic", "claude"):
                return call_anthropic_stage(prompt, payload_obj, api_key)
            return call_openai_compatible_stage(prompt, payload_obj, api_key)
        except Exception as exc:
            last_error = exc
            # Retry transient network errors AND malformed JSON: LLM JSON output is
            # stochastic (esp. without temperature control), so a re-ask often parses.
            retryable = is_retryable_error(exc) or isinstance(exc, json.JSONDecodeError)
            if idx >= attempts - 1 or not retryable:
                break
            time.sleep(min(20, 2 ** idx * 3))
    raise last_error or RuntimeError("AI call failed")


def call_openai_compatible_stage(prompt: str, payload_obj: Dict, api_key: str) -> Dict:
    base_url = os.getenv("AI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    wire_api = os.getenv("AI_WIRE_API", "chat").strip().lower()
    if wire_api == "responses":
        return call_openai_responses_stage(prompt, payload_obj, api_key, base_url)
    return call_openai_chat_stage(prompt, payload_obj, api_key, base_url)


def call_openai_chat_stage(prompt: str, payload_obj: Dict, api_key: str, base_url: str) -> Dict:
    model = os.getenv("AI_MODEL", "gpt-4.1-mini")
    payload = {
        "model": model,
        "temperature": 0.15,
        "max_tokens": int(os.getenv("AI_MAX_TOKENS", "4096")),
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": prompt},
            {"role": "user", "content": json.dumps(payload_obj, ensure_ascii=False)},
        ],
    }
    req = urllib.request.Request(
        f"{base_url}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=70) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    content = data["choices"][0]["message"]["content"]
    parsed = json.loads(content)
    return parsed if isinstance(parsed, dict) else {"events": parsed}


def call_openai_responses_stage(prompt: str, payload_obj: Dict, api_key: str, base_url: str) -> Dict:
    model = os.getenv("AI_MODEL", "gpt-4.1-mini")
    endpoint = _responses_endpoint(base_url)
    user_text = "请只输出 JSON 对象，不要 markdown。\n" + json.dumps(payload_obj, ensure_ascii=False)
    payload = {
        "model": model,
        "instructions": prompt,
        "input": user_text,
        "max_output_tokens": int(os.getenv("AI_MAX_TOKENS", "4096")),
    }
    if os.getenv("AI_RESPONSES_JSON_FORMAT", "false").strip().lower() in ("1", "true", "yes"):
        payload["text"] = {"format": {"type": "json_object"}}
    effort = os.getenv("AI_MONITOR_REASONING_EFFORT", os.getenv("AI_REASONING_EFFORT", "")).strip()
    if effort:
        payload["reasoning"] = {"effort": effort}
    if os.getenv("AI_DISABLE_RESPONSE_STORAGE", "").strip().lower() in ("1", "true", "yes"):
        payload["store"] = False
    data = _post_json(endpoint, payload, api_key, timeout=int(os.getenv("AI_TIMEOUT_SECONDS", "180")))
    text = _extract_responses_text(data)
    parsed = json.loads(_strip_json_fence(text))
    return parsed if isinstance(parsed, dict) else {"events": parsed}


def call_anthropic_stage(prompt: str, payload_obj: Dict, api_key: str) -> Dict:
    base_url = os.getenv("ANTHROPIC_BASE_URL", "https://api.anthropic.com").rstrip("/")
    model = os.getenv("ANTHROPIC_MODEL", os.getenv("AI_MODEL", "claude-3-5-sonnet-latest"))
    payload = {
        "model": model,
        "max_tokens": int(os.getenv("AI_MAX_TOKENS", "4096")),
        "system": prompt,
        "messages": [
            {"role": "user", "content": "请只输出 JSON 对象，不要 markdown。\n" + json.dumps(payload_obj, ensure_ascii=False)}
        ],
    }
    # Newer Claude models (opus-4.x / sonnet-4.x) reject `temperature`; only send it when explicitly set.
    temp = os.getenv("AI_TEMPERATURE", "").strip()
    if temp:
        payload["temperature"] = float(temp)
    headers = {
        "Content-Type": "application/json",
        "anthropic-version": os.getenv("ANTHROPIC_VERSION", "2023-06-01"),
    }
    # Gateways (Claude Code style) authenticate with a Bearer token; the public
    # Anthropic API uses x-api-key. Prefer Bearer when an auth token is present.
    auth_token = os.getenv("ANTHROPIC_AUTH_TOKEN", "").strip()
    if auth_token:
        headers["Authorization"] = f"Bearer {auth_token}"
    else:
        headers["x-api-key"] = api_key
    req = urllib.request.Request(
        f"{base_url}/v1/messages",
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    # _urlopen_json surfaces the HTTP error body (e.g. why a 400/502 happened)
    # instead of a bare "HTTP Error 400: Bad Request".
    data = _urlopen_json(req, timeout=int(os.getenv("AI_TIMEOUT_SECONDS", "180")))
    parts = data.get("content", [])
    text = "".join(part.get("text", "") for part in parts if part.get("type") == "text")
    parsed = json.loads(_strip_json_fence(text))
    return parsed if isinstance(parsed, dict) else {"events": parsed}


def _provider_api_key(provider: str) -> str:
    provider = provider.lower()
    if provider in ("anthropic", "claude"):
        return os.getenv("ANTHROPIC_AUTH_TOKEN", os.getenv("ANTHROPIC_API_KEY", os.getenv("CLAUDE_API_KEY", os.getenv("AI_API_KEY", "")))).strip()
    return os.getenv("AI_API_KEY", "").strip()


def _urlopen_json(req: urllib.request.Request, timeout: int) -> Dict:
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code}: {body[:1000]}") from exc


def _post_json(endpoint: str, payload: Dict, api_key: str, timeout: int) -> Dict:
    req = urllib.request.Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        method="POST",
    )
    return _urlopen_json(req, timeout=timeout)


def _classify_error(exc: Exception) -> str:
    """Coarse error category for ai_status, so dashboards/logs can tell apart
    a malformed AI response from a network/server problem."""
    if isinstance(exc, json.JSONDecodeError):
        return "bad_json"
    if is_retryable_error(exc):
        return "network"
    return "other"


def _strip_json_fence(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
    return text.strip()


def _responses_endpoint(base_url: str) -> str:
    base = base_url.rstrip("/")
    if base.endswith("/v1"):
        return f"{base}/responses"
    return f"{base}/v1/responses"


def _extract_responses_text(data: Dict) -> str:
    if isinstance(data.get("output_text"), str):
        return data["output_text"]
    chunks: List[str] = []
    for item in data.get("output", []) or []:
        for content in item.get("content", []) or []:
            if isinstance(content.get("text"), str):
                chunks.append(content["text"])
    return "".join(chunks)


def _candidate_payload(c: Candidate) -> Dict:
    return {
        "candidate_id": c.id,
        "title": c.title,
        "source_name": c.source_name,
        "source_confidence": c.confidence,
        "region": c.region,
        "language": c.language,
        "source_market": c.source_market,
        "source_weight": c.weight,
        "published_at": c.published_at,
        "url": c.url,
        "summary": c.summary,
        "content": c.content[:AI_CONTENT_LEN],
    }


def _events_from_ai(candidates: List[Candidate], items: List[Dict], keywords: Dict) -> List[MarketEvent]:
    by_id = {c.id: c for c in candidates}
    out: List[MarketEvent] = []
    used = set()
    for item in items:
        c = by_id.get(item.get("candidate_id", ""))
        if not c:
            continue
        used.add(c.id)
        out.append(_event_from_ai(c, item, keywords))
    for c in candidates:
        if c.id not in used:
            e = fallback_event(c, keywords)
            e.push_decision = "dashboard_only"
            out.append(e)
    return out


def _normalize_brand(raw, keywords: Dict) -> str:
    """把 AI 给的品牌名归一化到规范名：中文/别名（影石/大疆/EOS…）映射回
    Insta360/DJI/Canon…；未知但非空的品牌（Adobe/Tamron 等）原样保留。"""
    raw = str(raw or "").strip()
    if not raw:
        return "Other"
    canonical = detect_brand(raw, keywords)
    return canonical if canonical != "Other" else raw


def _event_from_ai(c: Candidate, item: Dict, keywords: Dict) -> MarketEvent:
    details = item.get("details_zh") or item.get("details") or []
    if isinstance(details, str):
        details = [details]
    # 先按对象清洗 AI 返回的 evidence；若清洗后为空（AI 常把 evidence 返回成字符串数组），
    # 回退到候选自身的 url，保证每条事件都有来源链接、且不会因“无证据”被误降为 dashboard_only。
    raw_evidence = item.get("evidence") or []
    evidence = [_clean_evidence(ev, c) for ev in raw_evidence if isinstance(ev, dict)][:5]
    if not evidence and c.url:
        evidence = [{"source_name": c.source_name, "url": c.url, "snippet": (c.summary or c.title)[:240]}]
    confidence = _choice(item.get("confidence"), {"official", "media", "community", "rumor"}, c.confidence)
    threat = _choice(item.get("threat_level"), {"high", "medium", "low"}, "low")
    push_decision = _choice(item.get("push_decision"), {"push", "dashboard_only", "discard"}, "dashboard_only")
    importance = _int_range(item.get("importance_score"), 1, 5, 1)
    confidence_score = _float_range(item.get("confidence_score"), 0.0, 1.0, 0.5)
    title_original = str(item.get("title_original") or c.title)
    title_zh = str(item.get("title_zh") or c.title)
    summary_zh = str(item.get("summary_zh") or c.summary or c.title)
    insight_zh = str(item.get("insight_zh") or "")
    return MarketEvent(
        id=stable_id(c.source_id, title_original, c.url),
        title=title_zh[:TITLE_LEN],
        brand=_normalize_brand(item.get("brand") or c.raw.get("brand"), keywords),
        category=_choice(item.get("category"), {"hardware", "software", "marketing", "industry", "pmf", "community"}, "industry"),
        event_type=str(item.get("event_type") or "行业趋势"),
        summary=summary_zh[:SUMMARY_LEN],
        details=[str(d)[:DETAIL_LEN] for d in details[:3]] or [summary_zh[:120]],
        insight=insight_zh[:INSIGHT_LEN],
        threat_level=threat,
        heat_score=importance,
        confidence=confidence,
        published_at=c.published_at,
        source_name=c.source_name,
        source_url=c.url,
        week_tag=week_tag(c.published_at),
        source_id=c.source_id,
        ai_confidence=confidence_score,
        title_zh=title_zh[:TITLE_LEN],
        title_original=title_original[:TITLE_ORIGINAL_LEN],
        language=str(item.get("language") or c.language),
        region=_choice(item.get("region"), {"domestic", "global"}, c.region),
        source_market=str(item.get("source_market") or c.source_market),
        summary_zh=summary_zh[:SUMMARY_LEN],
        details_zh=[str(d)[:DETAIL_LEN] for d in details[:3]] or [summary_zh[:120]],
        insight_zh=insight_zh[:INSIGHT_LEN],
        importance_score=importance,
        confidence_score=confidence_score,
        push_decision=push_decision,
        evidence=evidence,
        source_weight=c.weight,
    )


def _clean_evidence(item: Dict, c: Candidate) -> Dict:
    return {
        "source_name": str(item.get("source_name") or c.source_name),
        "url": str(item.get("url") or c.url),
        "snippet": str(item.get("snippet") or c.summary or c.title)[:240],
    }


def _choice(value, allowed: set, default: str) -> str:
    return value if isinstance(value, str) and value in allowed else default


def _int_range(value, low: int, high: int, default: int) -> int:
    try:
        return max(low, min(high, int(value)))
    except Exception:
        return default


def _float_range(value, low: float, high: float, default: float) -> float:
    try:
        return round(max(low, min(high, float(value))), 2)
    except Exception:
        return default
