from __future__ import annotations

import json
import os
import re
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from typing import Dict, List, Optional

from .constants import (
    HTTP_RETRY_ATTEMPTS,
    HTTP_TIMEOUT_SECONDS,
    MAX_ITEMS_PER_SOURCE,
    MAX_RESPONSE_BYTES,
    CONTENT_FETCH_LEN,
    SUMMARY_FETCH_LEN,
)
from .log import get_logger
from .models import Candidate
from .util import clean_text, is_retryable_error, parse_date, stable_id

USER_AGENT = "ImagingMonitor/0.1 (+https://github.com/) AppleWebKit/537.36"
log = get_logger(__name__)


def fetch_all(config: Dict, fixture_dir: str = "") -> tuple[List[Candidate], List[Dict]]:
    candidates: List[Candidate] = []
    health: List[Dict] = []

    for src in config.get("sources", []):
        if not src.get("enabled", True):
            continue
        try:
            items = fetch_source(src, fixture_dir)
            candidates.extend(items)
            health.append({"source_id": src["id"], "name": src["name"], "ok": True, "count": len(items), "region": src.get("region", "global"), "language": src.get("language", "en")})
        except Exception as exc:
            health.append({"source_id": src.get("id", ""), "name": src.get("name", ""), "ok": False, "error": str(exc), "count": 0, "region": src.get("region", "global"), "language": src.get("language", "en")})

    for app in config.get("apps", []):
        if not app.get("enabled", True):
            continue
        try:
            item = fetch_app_store(app, fixture_dir)
            if item:
                candidates.append(item)
                count = 1
            else:
                count = 0
            health.append({"source_id": app["id"], "name": app["name"], "ok": True, "count": count, "region": app.get("region", "global"), "language": app.get("language", "en")})
        except Exception as exc:
            health.append({"source_id": app.get("id", ""), "name": app.get("name", ""), "ok": False, "error": str(exc), "count": 0, "region": app.get("region", "global"), "language": app.get("language", "en")})

    return candidates, health


def fetch_source(src: Dict, fixture_dir: str = "") -> List[Candidate]:
    src_type = src.get("type", "rss")
    body = _fixture_or_http(src["id"], src["url"], fixture_dir)
    if src_type == "rss":
        return parse_rss(body, src)
    if src_type == "html":
        return parse_html_links(body, src)
    raise ValueError(f"Unsupported source type: {src_type}")


def parse_rss(body: str, src: Dict) -> List[Candidate]:
    root = ET.fromstring(body)
    items = []
    rss_items = root.findall(".//item")
    if not rss_items:
        rss_items = root.findall("{http://www.w3.org/2005/Atom}entry")
    for item in rss_items[:MAX_ITEMS_PER_SOURCE]:
        title = _xml_text(item, "title")
        link = _xml_text(item, "link")
        if not link:
            link_el = item.find("{http://www.w3.org/2005/Atom}link")
            link = link_el.attrib.get("href", "") if link_el is not None else ""
        published = _xml_text(item, "pubDate") or _xml_text(item, "published") or _xml_text(item, "updated")
        summary = _xml_text(item, "description") or _xml_text(item, "summary") or _xml_text(item, "content")
        title = clean_text(title)
        if not title or not link:
            continue
        items.append(Candidate(
            id=stable_id(src["id"], title, link),
            title=title,
            url=link,
            source_id=src["id"],
            source_name=src["name"],
            confidence=src.get("confidence", "media"),
            region=src.get("region", "global"),
            language=src.get("language", "en"),
            source_market=src.get("market", "Global"),
            weight=int(src.get("weight", 1) or 1),
            published_at=parse_date(published),
            summary=clean_text(summary)[:SUMMARY_FETCH_LEN],
            content=clean_text(summary)[:CONTENT_FETCH_LEN],
        ))
    return items


def parse_html_links(body: str, src: Dict) -> List[Candidate]:
    title_re = re.compile(r"<a[^>]+href=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>", re.I | re.S)
    seen = set()
    items: List[Candidate] = []
    for href, text in title_re.findall(body):
        title = clean_text(text)
        if len(title) < 12:
            continue
        url = urllib.parse.urljoin(src["url"], href)
        key = stable_id(src["id"], title, url)
        if key in seen:
            continue
        seen.add(key)
        items.append(Candidate(
            id=key,
            title=title,
            url=url,
            source_id=src["id"],
            source_name=src["name"],
            confidence=src.get("confidence", "media"),
            region=src.get("region", "global"),
            language=src.get("language", "en"),
            source_market=src.get("market", "Global"),
            weight=int(src.get("weight", 1) or 1),
            content=title,
        ))
        if len(items) >= MAX_ITEMS_PER_SOURCE:
            break
    return items


def fetch_app_store(app: Dict, fixture_dir: str = "") -> Optional[Candidate]:
    url = f"https://itunes.apple.com/lookup?id={app['app_id']}&country={app.get('country', 'us')}"
    body = _fixture_or_http(app["id"], url, fixture_dir)
    data = json.loads(body)
    results = data.get("results") or []
    if not results:
        return None
    r = results[0]
    version = r.get("version", "")
    date = parse_date(r.get("currentVersionReleaseDate", ""))
    notes = clean_text(r.get("releaseNotes", ""))
    title = f"{app['name']} iOS 更新至 {version}".strip()
    return Candidate(
        id=stable_id(app["id"], version, date),
        title=title,
        url=r.get("trackViewUrl", ""),
        source_id=app["id"],
        source_name="App Store",
        confidence="official",
        region=app.get("region", "global"),
        language=app.get("language", "en"),
        source_market=app.get("market", app.get("country", "Global").upper()),
        weight=int(app.get("weight", 5) or 5),
        published_at=date,
        summary=notes[:600],
        content=f"{title}\n{notes}",
        raw={"brand": app.get("brand", ""), "version": version, "app_name": app["name"]},
    )


def _xml_text(item: ET.Element, name: str) -> str:
    found = item.find(name)
    if found is None:
        found = item.find(f"{{http://www.w3.org/2005/Atom}}{name}")
    return "".join(found.itertext()) if found is not None else ""


def _fixture_or_http(source_id: str, url: str, fixture_dir: str) -> str:
    if fixture_dir:
        for ext in (".xml", ".rss", ".html", ".json"):
            path = os.path.join(fixture_dir, source_id + ext)
            if os.path.exists(path):
                with open(path, encoding="utf-8") as f:
                    return f.read()
        raise FileNotFoundError(f"No fixture for {source_id}")
    return _http_get(url)


def _http_get(url: str) -> str:
    """Fetch a URL with bounded retries on transient errors and a size cap."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    last_error: Optional[Exception] = None
    for attempt in range(HTTP_RETRY_ATTEMPTS + 1):
        try:
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_SECONDS) as resp:
                raw = resp.read(MAX_RESPONSE_BYTES + 1)
            if len(raw) > MAX_RESPONSE_BYTES:
                raise ValueError(f"response exceeds {MAX_RESPONSE_BYTES} bytes")
            return raw.decode("utf-8", errors="replace")
        except Exception as exc:  # noqa: BLE001 - retry transient, re-raise terminal
            last_error = exc
            if attempt >= HTTP_RETRY_ATTEMPTS or not is_retryable_error(exc):
                break
            backoff = min(8, 2 ** attempt)
            log.debug("fetch %s failed (%s), retrying in %ss", url, exc, backoff)
            time.sleep(backoff)
    raise last_error or RuntimeError(f"failed to fetch {url}")
