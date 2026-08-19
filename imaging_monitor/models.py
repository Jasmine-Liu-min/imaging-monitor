from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Dict, List


@dataclass
class Candidate:
    id: str
    title: str
    url: str
    source_id: str
    source_name: str
    confidence: str
    region: str = "global"
    language: str = "en"
    source_market: str = "Global"
    weight: int = 1
    published_at: str = ""
    summary: str = ""
    content: str = ""
    raw: Dict = field(default_factory=dict)


@dataclass
class MarketEvent:
    id: str
    title: str
    brand: str
    category: str
    event_type: str
    summary: str
    details: List[str]
    insight: str
    threat_level: str
    heat_score: int
    confidence: str
    published_at: str
    source_name: str
    source_url: str
    week_tag: str
    source_id: str = ""
    ai_confidence: float = 0.0
    pushed: bool = False
    title_zh: str = ""
    title_original: str = ""
    language: str = "en"
    region: str = "global"
    source_market: str = "Global"
    summary_zh: str = ""
    details_zh: List[str] = field(default_factory=list)
    insight_zh: str = ""
    importance_score: int = 1
    confidence_score: float = 0.0
    push_decision: str = "dashboard_only"
    evidence: List[Dict] = field(default_factory=list)
    source_weight: int = 1
    first_seen: str = ""
    is_new: bool = False

    def to_dict(self) -> Dict:
        data = asdict(self)
        data["title"] = self.title or self.title_zh or self.title_original
        data["summary"] = self.summary or self.summary_zh
        data["details"] = self.details or self.details_zh
        data["insight"] = self.insight or self.insight_zh
        data["source_url"] = self.source_url or (self.evidence[0]["url"] if self.evidence else "")
        return data
