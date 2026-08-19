from __future__ import annotations

import argparse
import json
import os
from typing import Dict

from .ai import extract_events
from .ai import call_stage
from .config import load_simple_yaml, project_root, validate_keywords, validate_sources
from .fetchers import fetch_all
from .feishu import send_report
from .log import configure as configure_logging, get_logger
from .report import build_report, write_outputs
from .rules import filter_candidates
from .store import append_run, build_trend, load_store, prune_store, save_store, update_store
from .util import utc_now_iso

log = get_logger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(description="Imaging competitor monitor")
    sub = parser.add_subparsers(dest="cmd", required=True)

    run_p = sub.add_parser("run", help="fetch, extract, report, optionally push")
    run_p.add_argument("--config-dir", default="config")
    run_p.add_argument("--out-dir", default="data")
    run_p.add_argument("--fixture-dir", default="")
    run_p.add_argument("--dry-run", action="store_true")
    run_p.add_argument("--max-ai-items", type=int, default=30)  # Opus 单批超过此量易超时
    run_p.add_argument("--window-hours", type=int, default=17)
    run_p.add_argument("--skip-push", action="store_true")
    run_p.add_argument("--no-ai", action="store_true", help="disable AI calls for deterministic local tests")
    run_p.add_argument("--verbose", action="store_true", help="enable debug logging to stderr")

    ping_p = sub.add_parser("ping-ai", help="test configured AI provider with a tiny JSON request")
    ping_p.add_argument("--prompt", default="请返回 {\"ok\": true, \"message\": \"pong\"}。只输出 JSON。")

    send_p = sub.add_parser("send", help="send the latest data/report.json card to Feishu (test webhook)")
    send_p.add_argument("--out-dir", default="data")
    send_p.add_argument("--dry-run", action="store_true", help="build the card but do not actually POST")

    args = parser.parse_args()
    if args.cmd == "run":
        result = run(args)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif args.cmd == "send":
        root = project_root()
        load_env_file(os.path.join(root, ".env"))
        report_path = os.path.join(_abs(root, args.out_dir), "report.json")
        if not os.path.exists(report_path):
            print(json.dumps({"sent": False, "error": f"no report at {report_path}; run first"}, ensure_ascii=False, indent=2))
            return
        with open(report_path, encoding="utf-8") as f:
            report = json.load(f)
        push = send_report(report, dry_run=args.dry_run)
        print(json.dumps({k: v for k, v in push.items() if k != "card"}, ensure_ascii=False, indent=2))
    elif args.cmd == "ping-ai":
        root = project_root()
        load_env_file(os.path.join(root, ".env"))
        provider = os.getenv("AI_PROVIDER", "openai-compatible")
        is_anthropic = provider in ("anthropic", "claude")
        if is_anthropic:
            api_key = os.getenv("ANTHROPIC_AUTH_TOKEN") or os.getenv("ANTHROPIC_API_KEY") or os.getenv("AI_API_KEY", "")
        else:
            api_key = os.getenv("AI_API_KEY", "")
        meta = {
            "provider": provider,
            "model": os.getenv("ANTHROPIC_MODEL" if is_anthropic else "AI_MODEL", ""),
            "base_url": os.getenv("ANTHROPIC_BASE_URL" if is_anthropic else "AI_BASE_URL", ""),
            "wire_api": "anthropic" if is_anthropic else os.getenv("AI_WIRE_API", "chat"),
            "has_key": bool(api_key),
        }
        if not api_key:
            print(json.dumps({"ok": False, "error": "missing AI API key", **meta}, ensure_ascii=False, indent=2))
            return
        try:
            result = call_stage(args.prompt, {"ping": True}, api_key, provider)
            print(json.dumps({"ok": True, "result": result, **meta}, ensure_ascii=False, indent=2))
        except Exception as exc:
            print(json.dumps({"ok": False, "error": str(exc), **meta}, ensure_ascii=False, indent=2))
        return


def run(args) -> Dict:
    configure_logging(getattr(args, "verbose", False))
    root = project_root()
    load_env_file(os.path.join(root, ".env"))
    if getattr(args, "no_ai", False):
        os.environ["AI_PROVIDER"] = "none"
        os.environ["AI_API_KEY"] = ""
        os.environ["ANTHROPIC_API_KEY"] = ""
        os.environ["CLAUDE_API_KEY"] = ""
    config_dir = _abs(root, args.config_dir)
    out_dir = _abs(root, args.out_dir)
    fixture_dir = _abs(root, args.fixture_dir) if args.fixture_dir else ""

    sources = validate_sources(load_simple_yaml(os.path.join(config_dir, "sources.yaml")))
    keywords = validate_keywords(load_simple_yaml(os.path.join(config_dir, "keywords.yaml")))

    candidates, health = fetch_all(sources, fixture_dir=fixture_dir)
    log.info("fetched %d candidates from %d sources", len(candidates), len(health))
    filtered = filter_candidates(candidates, keywords, limit=args.max_ai_items)
    events, ai_status = extract_events(filtered, keywords)

    # Cross-run memory: dedup against prior runs so only genuinely new events push.
    generated_at = utc_now_iso()
    store = load_store(out_dir)
    store, new_ids, updated_ids = update_store(store, events, generated_at)

    report = build_report(
        events,
        health,
        ai_status,
        window_hours=args.window_hours,
        new_ids=new_ids,
        updated_ids=updated_ids,
        trend=build_trend(store),
        keywords=keywords,
    )

    push = {"sent": False, "reason": "skip_push"}
    if not args.skip_push:
        if report["push_events"] or args.dry_run:
            push = send_report(report, dry_run=args.dry_run)
        else:
            push = {"sent": False, "reason": "no_new_events"}

    append_run(store, {
        "generated_at": generated_at,
        "candidate_count": len(events),
        "new_count": len(new_ids),
        "updated_count": len(updated_ids),
        "pushable_count": report["summary"]["pushable_count"],
        "pushed_count": len(report["push_events"]) if push.get("sent") else 0,
        "source_ok": report["summary"]["source_ok"],
        "source_failed": report["summary"]["source_failed"],
    })
    prune_store(store, generated_at)
    save_store(store, out_dir)
    # Re-embed the freshly appended run so the dashboard trend includes this cycle.
    report["trend"] = build_trend(store)
    write_outputs(events, report, out_dir)

    log.info(
        "run done: candidates=%d filtered=%d events=%d new=%d updated=%d pushed=%s ai=%s",
        len(candidates), len(filtered), len(events), len(new_ids), len(updated_ids),
        push.get("sent"), ai_status.get("stage"),
    )

    return {
        "candidates": len(candidates),
        "filtered": len(filtered),
        "events": len(events),
        "new": len(new_ids),
        "updated": len(updated_ids),
        "out_dir": out_dir,
        "push": {k: v for k, v in push.items() if k != "card"},
        "ai": ai_status,
    }


def _abs(root: str, path: str) -> str:
    return path if os.path.isabs(path) else os.path.join(root, path)


def load_env_file(path: str) -> None:
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = _strip_env_comment(value.strip()).strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


def _strip_env_comment(value: str) -> str:
    in_single = False
    in_double = False
    for idx, ch in enumerate(value):
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        elif ch == "#" and not in_single and not in_double:
            if idx == 0 or value[idx - 1].isspace():
                return value[:idx].rstrip()
    return value


if __name__ == "__main__":
    main()
