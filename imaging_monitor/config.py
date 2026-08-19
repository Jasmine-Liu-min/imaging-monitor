from __future__ import annotations

import os
import re
from typing import Any, Dict, List


def load_simple_yaml(path: str) -> Dict[str, Any]:
    """Load a small YAML subset used by this project.

    The parser intentionally supports only nested dictionaries/lists and inline
    string arrays. It keeps the project dependency-free for GitHub Actions.
    """
    with open(path, encoding="utf-8") as f:
        lines = f.readlines()
    root: Dict[str, Any] = {}
    stack: List[tuple[int, Any]] = [(-1, root)]

    for idx, raw in enumerate(lines):
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        text = raw.strip()
        while stack and indent <= stack[-1][0]:
            stack.pop()
        parent = stack[-1][1]

        if text.startswith("- "):
            item_text = text[2:].strip()
            if not isinstance(parent, list):
                raise ValueError(f"List item without list parent in {path}: {raw}")
            if ":" in item_text:
                key, value = item_text.split(":", 1)
                item: Dict[str, Any] = {key.strip(): _parse_scalar(value.strip())}
                parent.append(item)
                stack.append((indent, item))
            else:
                parent.append(_parse_scalar(item_text))
            continue

        key, value = text.split(":", 1)
        key = key.strip()
        value = value.strip()
        if value:
            parent[key] = _parse_scalar(value)
            continue

        next_container: Any = [] if _next_nonempty_starts_list(lines, idx) else {}
        parent[key] = next_container
        stack.append((indent, next_container))

    return root


def _next_nonempty_starts_list(lines: List[str], idx: int) -> bool:
    current = lines[idx]
    base_indent = len(current) - len(current.lstrip(" "))
    for line in lines[idx + 1:]:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip(" "))
        return indent > base_indent and line.strip().startswith("- ")
    return False


def _parse_scalar(value: str) -> Any:
    if value in ("true", "True"):
        return True
    if value in ("false", "False"):
        return False
    if value in ("null", "None", "~"):
        return None
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        if not inner:
            return []
        return [_strip_quotes(part.strip()) for part in _split_inline_list(inner)]
    return _strip_quotes(value)


def _split_inline_list(value: str) -> List[str]:
    parts = re.findall(r'"[^"]*"|\'[^\']*\'|[^,]+', value)
    return [p.strip() for p in parts if p.strip()]


def _strip_quotes(value: str) -> str:
    if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
        return value[1:-1]
    return value


def project_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class ConfigError(ValueError):
    """Raised when sources.yaml / keywords.yaml is missing required structure."""


def validate_sources(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Fail loudly on malformed source config instead of silently fetching nothing."""
    sources = cfg.get("sources", [])
    apps = cfg.get("apps", [])
    if not isinstance(sources, list) or not isinstance(apps, list):
        raise ConfigError("sources.yaml: 'sources' and 'apps' must be lists")
    for i, src in enumerate(sources):
        for key in ("id", "name", "url", "type"):
            if not (isinstance(src, dict) and src.get(key)):
                raise ConfigError(f"sources.yaml: source #{i} missing required '{key}'")
        if src["type"] not in ("rss", "html"):
            raise ConfigError(f"sources.yaml: source '{src['id']}' has unsupported type '{src['type']}'")
    for i, app in enumerate(apps):
        for key in ("id", "name", "app_id"):
            if not (isinstance(app, dict) and app.get(key)):
                raise ConfigError(f"sources.yaml: app #{i} missing required '{key}'")
    return cfg


def validate_keywords(cfg: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(cfg.get("brands", {}), dict):
        raise ConfigError("keywords.yaml: 'brands' must be a mapping")
    if not isinstance(cfg.get("categories", {}), dict):
        raise ConfigError("keywords.yaml: 'categories' must be a mapping")
    return cfg
