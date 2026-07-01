from __future__ import annotations
from typing import Literal, TypedDict


class Signal(TypedDict):
    type: str
    severity: Literal["info", "warning", "critical"]
    evidence: dict
    affected_entity: str
    confidence: float


class Recommendation(TypedDict):
    id: str
    title: str
    rationale: str
    priority: Literal["low", "medium", "high"]
    triggered_by: list
    actions: list
    metrics: dict


class RecommendationsBlob(TypedDict):
    generated_at: str
    snapshot_hash: str
    signals: list
    recommendations: list
