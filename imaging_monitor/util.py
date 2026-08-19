from __future__ import annotations

import hashlib
import html
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Iterable
from urllib.parse import urlparse


def stable_id(*parts: str) -> str:
    raw = "|".join((p or "").strip().lower() for p in parts)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def clean_text(value: str) -> str:
    value = html.unescape(value or "")
    value = re.sub(r"<[^>]+>", " ", value)
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def parse_date(value: str) -> str:
    if not value:
        return ""
    value = value.strip()
    try:
        dt = parsedate_to_datetime(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.date().isoformat()
    except Exception:
        pass
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%d %b %Y", "%B %d, %Y"):
        try:
            return datetime.strptime(value[:32], fmt).date().isoformat()
        except ValueError:
            continue
    found = re.search(r"20\d{2}[-/]\d{1,2}[-/]\d{1,2}", value)
    if found:
        return found.group(0).replace("/", "-")
    return ""


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def week_tag(date_value: str = "") -> str:
    try:
        dt = datetime.fromisoformat((date_value or utc_now_iso())[:10])
    except ValueError:
        dt = datetime.now(timezone.utc)
    year, week, _ = dt.isocalendar()
    return f"{year}-W{week:02d}"


def domain(url: str) -> str:
    return urlparse(url).netloc.lower().replace("www.", "")


_RETRYABLE_TOKENS = ("429", "500", "502", "503", "504", "timeout", "timed out", "temporarily", "concurrency limit", "connection reset", "urlopen error")


def is_retryable_error(exc: Exception) -> bool:
    """True for transient network/server errors worth retrying (shared by AI + fetchers)."""
    text = str(exc).lower()
    return any(token in text for token in _RETRYABLE_TOKENS)


def contains_any(text: str, words: Iterable[str]) -> bool:
    lowered = text.lower()
    return any((w or "").lower() in lowered for w in words)


def keyword_in_text(text: str, keyword: str) -> bool:
    keyword = (keyword or "").strip()
    if not keyword:
        return False
    lowered = text.lower()
    needle = keyword.lower()
    # Short model names such as X5 should not match unrelated strings like 4x5.
    if len(needle) <= 3 and re.fullmatch(r"[a-z0-9]+", needle):
        return re.search(rf"(?<![a-z0-9]){re.escape(needle)}(?![a-z0-9])", lowered) is not None
    return needle in lowered


def detect_language(text: str, default: str = "other") -> str:
    if re.search(r"[\u4e00-\u9fff]", text or ""):
        return "zh"
    if re.search(r"[A-Za-z]", text or ""):
        return "en"
    return default


def zh_title_from_original(title: str, brand: str, category: str, event_type: str) -> str:
    if detect_language(title) == "zh":
        return title
    prefix = brand if brand and brand != "Other" else "影像行业"
    return f"{prefix}｜{event_type}：{title}"
