from __future__ import annotations

import hashlib
import json
import logging
import uuid
from datetime import datetime, timezone

from .context_builder import build_dataset_context
from .signal_detectors import run_all_detectors
from .gemini_client import call_gemini
from .prompts import build_prompt

logger = logging.getLogger(__name__)


# ── Public entry point ────────────────────────────────────────────────────────

def run_recommendations_service(dataset_id: str, force: bool = False) -> dict:
    """
    Build (or return cached) recommendations for a dataset.

    Flow:
      1. build_dataset_context()  — gather existing analytics
      2. compute snapshot hash    — check if cache is still valid
      3. run_all_detectors()      — deterministic pattern detection
      4. call_gemini()            — convert signals → actionable text
      5. persist & return blob

    Args:
        dataset_id: UUID of the dataset.
        force:      Skip cache check and always regenerate.

    Returns:
        RecommendationsBlob dict with keys:
          generated_at, snapshot_hash, signals, recommendations
    """
    from api.supabase_client import get_dataset, update_dataset

    ctx = build_dataset_context(dataset_id)

    # ── Cache check ──────────────────────────────────────────────────────────
    snapshot_hash = _compute_snapshot_hash(ctx)
    if not force:
        cached = (ctx.get("global_context") or {}).get("recommendations")
        if isinstance(cached, str):
            try:
                cached = json.loads(cached)
            except Exception:
                cached = None
        if isinstance(cached, dict) and cached.get("snapshot_hash") == snapshot_hash:
            logger.info("Recommendations: cache hit for dataset %s", dataset_id)
            return cached

    # ── Pipeline ─────────────────────────────────────────────────────────────
    logger.info("Recommendations: generating for dataset %s (force=%s)", dataset_id, force)

    signals = run_all_detectors(ctx)
    logger.info("Recommendations: %d signal(s) detected", len(signals))

    if signals:
        prompt = build_prompt(ctx, signals)
        raw_recs = call_gemini(prompt)
    else:
        raw_recs = []
        logger.info("Recommendations: no signals fired — returning healthy state")

    recommendations = [_enrich(r, idx) for idx, r in enumerate(raw_recs)]

    blob = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "snapshot_hash": snapshot_hash,
        "signals": signals,
        "recommendations": recommendations,
    }

    # ── Persist ───────────────────────────────────────────────────────────────
    try:
        dataset = get_dataset(dataset_id)
        global_context = dataset.get("global_context") or {}
        if isinstance(global_context, str):
            try:
                global_context = json.loads(global_context)
            except Exception:
                global_context = {}
        global_context["recommendations"] = blob
        update_dataset(dataset_id, {"global_context": global_context})
        logger.info("Recommendations: persisted for dataset %s", dataset_id)
    except Exception as exc:
        logger.error("Recommendations: failed to persist blob: %s", exc)

    return blob


# ── Helpers ───────────────────────────────────────────────────────────────────

def _compute_snapshot_hash(ctx: dict) -> str:
    """
    Hash of the underlying analytics so we know when to regenerate.
    Changes when: a new forecast runs, segmentation re-runs, or step8 changes.
    """
    gc = ctx.get("global_context") or {}
    fc = ctx.get("forecast") or {}
    seg = ctx.get("segmentation") or {}
    step8 = ctx.get("step8") or {}

    parts = [
        str(fc.get("id") or fc.get("created_at") or ""),
        str(seg.get("generated_at") or seg.get("method") or ""),
        str(step8.get("generated_at") or ""),
        str(gc.get("step7", {}).get("generated_at") if isinstance(gc.get("step7"), dict) else ""),
    ]
    raw = "|".join(parts)
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


def _enrich(rec: dict, idx: int) -> dict:
    """Add a stable ID and normalise priority to the recommendation dict."""
    priority = str(rec.get("priority") or "medium").lower()
    if priority not in ("low", "medium", "high"):
        priority = "medium"
    return {
        "id":           rec.get("id") or str(uuid.uuid4()),
        "title":        rec.get("title") or f"Recommendation {idx + 1}",
        "rationale":    rec.get("rationale") or "",
        "priority":     priority,
        "triggered_by": rec.get("triggered_by") or [],
        "actions":      rec.get("actions") or [],
        "metrics":      rec.get("metrics") or {},
    }
