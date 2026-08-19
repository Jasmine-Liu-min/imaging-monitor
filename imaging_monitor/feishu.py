from __future__ import annotations

import json
import os
import urllib.request
from typing import Dict, List


def send_report(report: Dict, dry_run: bool = False) -> Dict:
    webhook = os.getenv("FEISHU_WEBHOOK_URL", "").strip()
    card = build_card(report)
    if dry_run or not webhook:
        return {"sent": False, "reason": "dry_run_or_missing_webhook", "card": card}

    req = urllib.request.Request(
        webhook,
        data=json.dumps(card).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        body = resp.read().decode("utf-8")
    return {"sent": True, "response": body}


_THREAT_ZH = {"high": "高威胁", "medium": "中威胁", "low": "低威胁"}


def _report_title(report: Dict) -> str:
    """按北京时间时段生成晨报/日报/晚报标题。"""
    period = "日报"
    try:
        hour = (int(report["generated_at"][11:13]) + 8) % 24  # UTC -> 北京时间
        period = "晨报" if hour < 12 else ("日报" if hour < 18 else "晚报")
    except Exception:
        pass
    return f"【竞品&市场信息】{report['generated_at'][:10]} {period} 🔔"


def build_card(report: Dict) -> Dict:
    title = _report_title(report)
    summary = report.get("summary", {})
    # Push the newly-surfaced events; fall back to top5 for backward compatibility.
    highlights = report.get("push_events") or report.get("top5", [])
    sections = _push_sections(report)
    battlecards = report.get("battlecards", [])
    dashboard_url = os.getenv("PUBLIC_DASHBOARD_URL", "").strip()

    elements: List[Dict] = [
        _markdown(f"**监控时间段：** {report['window']['start'][:16]} ~ {report['window']['end'][:16]} UTC"),
        _markdown(
            f"🆕 **今日新增：** {summary.get('new_count', 0)} 条"
            f"（可推送 {summary.get('new_pushable_count', 0)} 条）｜"
            f"**累计候选：** {summary.get('candidate_count', 0)} 条｜"
            f"**国内：** {summary.get('domestic_count', 0)} 条｜"
            f"**国外：** {summary.get('global_count', 0)} 条"
        ),
    ]

    summary_line = _battlecard_summary(battlecards)
    if summary_line:
        elements.extend([{"tag": "hr"}, _markdown("🎯 **重点竞品 battlecard**\n" + summary_line)])

    elements.extend([{"tag": "hr"}, _markdown("📌 **重要内容速览**")])
    if highlights:
        lines = []
        for idx, e in enumerate(highlights, 1):
            et = e.get("event_type") or "动态"
            lines.append(f"{idx}. 【{et}】{e.get('title_zh') or e['title']}")
        elements.append(_markdown("\n".join(lines)))
    else:
        elements.append(_markdown("本时段无新增高价值动态。"))

    section_defs = [
        ("domestic", "一、国内重点"),
        ("global", "二、国外重点"),
        ("software", "三、软件/App 更新"),
        ("community", "四、社区与用户反馈"),
        ("pmf", "五、场景/需求信号（PMF）"),
    ]
    for key, label in section_defs:
        items = sections.get(key, [])
        if not items:
            continue
        elements.extend([{"tag": "hr"}, _markdown(f"**{label}**")])
        for idx, e in enumerate(items[:4], 1):
            elements.append(_markdown(_event_markdown(idx, e)))

    quality = report.get("quality", {})
    elements.extend([
        {"tag": "hr"},
        _markdown(
            "📎 **质量状态**\n"
            f"- AI：{'正常' if quality.get('ai_ok') else '未启用/降级'}｜阶段：{quality.get('ai_stage', '-')}\n"
            f"- 无证据事件：{quality.get('events_without_evidence', 0)}｜丢弃：{quality.get('events_discarded', 0)}｜信源异常：{quality.get('source_failed', 0)}"
        ),
    ])

    failed = [h for h in report.get("health", []) if not h.get("ok")]
    if failed:
        elements.extend([{"tag": "hr"}, _markdown("⚠️ **信源异常**\n" + "\n".join(f"- {h.get('name')}: {h.get('error')}" for h in failed[:5]))])
    if dashboard_url:
        elements.append({
            "tag": "action",
            "actions": [{"tag": "button", "text": {"tag": "plain_text", "content": "打开看板"}, "url": dashboard_url, "type": "primary"}],
        })

    return {
        "msg_type": "interactive",
        "card": {
            "config": {"wide_screen_mode": True},
            "header": {"template": "turquoise", "title": {"tag": "plain_text", "content": title}},
            "elements": elements,
        },
    }


def _push_sections(report: Dict) -> Dict:
    push_events = report.get("push_events") or []
    if not push_events:
        return report.get("sections", {})
    return {
        "domestic": [e for e in push_events if e.get("region") == "domestic" and e.get("category") not in ("software", "community")][:6],
        "global": [e for e in push_events if e.get("region") == "global" and e.get("category") not in ("software", "community")][:6],
        "software": [e for e in push_events if e.get("category") == "software"][:6],
        "community": [e for e in push_events if e.get("category") == "community" or e.get("confidence") == "community"][:6],
        "pmf": [e for e in push_events if e.get("category") == "pmf"][:6],
    }


def _battlecard_summary(battlecards: List[Dict]) -> str:
    threat_label = {"high": "🔴高", "medium": "🟡中", "low": "🟢低"}
    lines = []
    for c in battlecards[:4]:
        badge = f"（新增 {c.get('new_count', 0)}）" if c.get("new_count") else ""
        insight = (c.get("key_insight_zh") or "").strip()
        if c.get("is_self"):
            tag = "🏠 自家"
        else:
            tag = "威胁 " + threat_label.get(c.get("threat_summary", ""), c.get("threat_summary", ""))
        lines.append(
            f"- **{c.get('brand')}**｜{c.get('event_count', 0)} 条{badge}｜{tag}\n  {insight}"
        )
    return "\n".join(lines)


def _markdown(content: str) -> Dict:
    return {"tag": "markdown", "content": content}


def _event_markdown(idx: int, e: Dict) -> str:
    evidence = e.get("evidence") or []
    first = evidence[0] if evidence else {"url": e.get("source_url", ""), "source_name": e.get("source_name", "")}
    url = first.get("url") or e.get("source_url") or ""
    platform = first.get("source_name") or e.get("source_name") or "—"
    imp = max(1, min(5, int(e.get("importance_score", 1) or 1)))
    stars = "★" * imp + "☆" * (5 - imp)
    threat = _THREAT_ZH.get(e.get("threat_level", ""), e.get("threat_level", ""))
    content = e.get("summary_zh") or e.get("summary", "")
    details = "；".join((e.get("details_zh") or e.get("details") or [])[:2])
    if details and details not in content:
        content = f"{content}（{details}）"
    brand = e.get("brand") or "—"
    return (
        f"🛰️ **{idx}. 【{brand}】{e.get('title_zh') or e.get('title')}**\n"
        f"**热度：** {stars}\n"
        f"**报道平台：** {platform}\n"
        f"**威胁等级：** {threat}\n"
        f"**事件类型：** {e.get('event_type') or '行业趋势'}\n"
        f"**内容：** {content}\n"
        f"**洞察解读：** {e.get('insight_zh') or e.get('insight', '')}\n"
        f"**来源链接：** {url}"
    )
