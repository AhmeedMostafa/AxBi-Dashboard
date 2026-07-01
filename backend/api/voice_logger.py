"""
Voice/TTS request logger.

Records every TTS, translation, and overview generation request to disk so the
team can audit "what text went in, what audio came out, what went wrong."

Layout (all under BASE_DIR/logs/voice/):

    index/<user_id>.jsonl                   one line per request, append-only
    audio/<YYYY-MM-DD>/<entry_id>.mp3       the raw audio bytes (only TTS kind)

Each JSONL line is a JSON object — see `_serialize_entry()` for the schema.

Disabled if VOICE_LOG_ENABLED=0. Storage of audio can be turned off with
VOICE_LOG_KEEP_AUDIO=0. Per-user JSONL files are trimmed at
VOICE_LOG_MAX_ENTRIES_PER_USER lines (default 500).
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from django.conf import settings


# ── Configuration ────────────────────────────────────────────────────────────

VOICE_LOG_ENABLED = (os.environ.get("VOICE_LOG_ENABLED", "1") or "1").strip() not in ("0", "false", "no")
VOICE_LOG_KEEP_AUDIO = (os.environ.get("VOICE_LOG_KEEP_AUDIO", "1") or "1").strip() not in ("0", "false", "no")
try:
    VOICE_LOG_MAX_ENTRIES_PER_USER = max(50, int(os.environ.get("VOICE_LOG_MAX_ENTRIES_PER_USER", "500")))
except ValueError:
    VOICE_LOG_MAX_ENTRIES_PER_USER = 500
try:
    VOICE_LOG_AUDIO_RETENTION_DAYS = max(1, int(os.environ.get("VOICE_LOG_AUDIO_RETENTION_DAYS", "7")))
except ValueError:
    VOICE_LOG_AUDIO_RETENTION_DAYS = 7

BASE_DIR: Path = Path(getattr(settings, "BASE_DIR", Path(__file__).resolve().parent.parent))
VOICE_LOG_ROOT: Path = BASE_DIR / "logs" / "voice"
INDEX_DIR: Path = VOICE_LOG_ROOT / "index"
AUDIO_DIR: Path = VOICE_LOG_ROOT / "audio"

_index_locks: dict[str, threading.Lock] = {}
_index_locks_master = threading.Lock()


def _safe_user_id(user_id: str) -> str:
    """Allow only filesystem-safe characters in the user id (prevents traversal)."""
    return re.sub(r"[^A-Za-z0-9_\-]", "_", (user_id or "anon"))[:64] or "anon"


def _safe_entry_id(entry_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9_\-]", "", (entry_id or ""))[:64]


def _lock_for(user_id: str) -> threading.Lock:
    with _index_locks_master:
        lock = _index_locks.get(user_id)
        if lock is None:
            lock = threading.Lock()
            _index_locks[user_id] = lock
        return lock


def _ensure_dirs() -> None:
    INDEX_DIR.mkdir(parents=True, exist_ok=True)
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)


def _index_file(user_id: str) -> Path:
    return INDEX_DIR / f"{_safe_user_id(user_id)}.jsonl"


def _truncate_text(text: Any, max_chars: int = 12000) -> str:
    if not isinstance(text, str):
        text = "" if text is None else str(text)
    if len(text) > max_chars:
        return text[:max_chars] + f"\n…[truncated {len(text) - max_chars} chars]"
    return text


def _trim_index(user_id: str) -> None:
    """Keep at most VOICE_LOG_MAX_ENTRIES_PER_USER lines in the user's index."""
    path = _index_file(user_id)
    if not path.exists():
        return
    try:
        with path.open("rb") as f:
            lines = f.readlines()
        if len(lines) <= VOICE_LOG_MAX_ENTRIES_PER_USER:
            return
        keep = lines[-VOICE_LOG_MAX_ENTRIES_PER_USER:]
        dropped = lines[: len(lines) - VOICE_LOG_MAX_ENTRIES_PER_USER]
        tmp = path.with_suffix(".jsonl.tmp")
        with tmp.open("wb") as f:
            f.writelines(keep)
        os.replace(tmp, path)
        for raw in dropped:
            try:
                obj = json.loads(raw.decode("utf-8"))
            except Exception:
                continue
            audio_path = obj.get("audio_path")
            if audio_path:
                try:
                    (VOICE_LOG_ROOT / audio_path).unlink(missing_ok=True)
                except Exception:
                    pass
    except Exception:
        pass


def _prune_old_audio() -> None:
    """Delete audio files older than VOICE_LOG_AUDIO_RETENTION_DAYS days."""
    if not AUDIO_DIR.exists():
        return
    cutoff = time.time() - VOICE_LOG_AUDIO_RETENTION_DAYS * 86400
    try:
        for day_dir in AUDIO_DIR.iterdir():
            if not day_dir.is_dir():
                continue
            empty = True
            for f in day_dir.iterdir():
                try:
                    if f.stat().st_mtime < cutoff:
                        f.unlink(missing_ok=True)
                    else:
                        empty = False
                except Exception:
                    empty = False
            if empty:
                try:
                    day_dir.rmdir()
                except Exception:
                    pass
    except Exception:
        pass


_last_prune_ts = 0.0


def _maybe_prune() -> None:
    """Run audio retention at most once an hour."""
    global _last_prune_ts
    now = time.time()
    if now - _last_prune_ts > 3600:
        _last_prune_ts = now
        try:
            _prune_old_audio()
        except Exception:
            pass


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _new_entry_id() -> str:
    return uuid.uuid4().hex[:16]


# ── Public API ───────────────────────────────────────────────────────────────


def log_tts_request(
    *,
    user_id: str,
    raw_text: str,
    stripped_text: str,
    language: str,
    voice: str,
    speaking_rate: Optional[float] = None,
    pitch: Optional[float] = None,
    audio_bytes: Optional[bytes] = None,
    duration_ms: Optional[int] = None,
    status_code: int = 200,
    error: Optional[str] = None,
    extra: Optional[dict] = None,
) -> Optional[str]:
    """Record one TTS attempt. Returns the entry id (or None if logging disabled)."""
    if not VOICE_LOG_ENABLED:
        return None
    try:
        _ensure_dirs()
        _maybe_prune()
        entry_id = _new_entry_id()
        audio_rel_path = None
        audio_kb = 0

        file_ext = "mp3"
        if extra and isinstance(extra, dict):
            file_ext = (extra.get("audio_format") or "mp3").strip().lower() or "mp3"
        if file_ext not in ("mp3", "wav", "ogg"):
            file_ext = "mp3"

        if audio_bytes and VOICE_LOG_KEEP_AUDIO:
            day = _today()
            day_dir = AUDIO_DIR / day
            day_dir.mkdir(parents=True, exist_ok=True)
            audio_file = day_dir / f"{_safe_user_id(user_id)}_{entry_id}.{file_ext}"
            try:
                audio_file.write_bytes(audio_bytes)
                audio_rel_path = f"audio/{day}/{audio_file.name}"
                audio_kb = len(audio_bytes) // 1024
            except Exception:
                audio_rel_path = None

        entry = {
            "id": entry_id,
            "kind": "tts",
            "ts": _now_iso(),
            "user_id": _safe_user_id(user_id),
            "language": (language or "")[:32],
            "voice": (voice or "")[:120],
            "speaking_rate": speaking_rate,
            "pitch": pitch,
            "input_chars": len(raw_text or ""),
            "stripped_chars": len(stripped_text or ""),
            "raw_text": _truncate_text(raw_text),
            "stripped_text": _truncate_text(stripped_text),
            "status": status_code,
            "duration_ms": duration_ms,
            "audio_bytes": len(audio_bytes) if audio_bytes else 0,
            "audio_kb": audio_kb,
            "audio_path": audio_rel_path,
            "error": (error or None) if error else None,
        }
        if extra and isinstance(extra, dict):
            entry["extra"] = {k: extra[k] for k in list(extra)[:20]}

        _append_index(user_id, entry)
        return entry_id
    except Exception:
        return None


def log_translate_request(
    *,
    user_id: str,
    source_text: str,
    output_text: str,
    target_language: str,
    style: str = "formal",
    model: str = "",
    duration_ms: Optional[int] = None,
    status_code: int = 200,
    error: Optional[str] = None,
) -> Optional[str]:
    if not VOICE_LOG_ENABLED:
        return None
    try:
        _ensure_dirs()
        entry_id = _new_entry_id()
        entry = {
            "id": entry_id,
            "kind": "translate",
            "ts": _now_iso(),
            "user_id": _safe_user_id(user_id),
            "target_language": (target_language or "")[:32],
            "style": (style or "")[:32],
            "model": (model or "")[:64],
            "input_chars": len(source_text or ""),
            "output_chars": len(output_text or ""),
            "raw_text": _truncate_text(source_text),
            "output_text": _truncate_text(output_text),
            "status": status_code,
            "duration_ms": duration_ms,
            "error": (error or None) if error else None,
        }
        _append_index(user_id, entry)
        return entry_id
    except Exception:
        return None


def log_overview_request(
    *,
    user_id: str,
    dataset_id: str,
    output_text: str,
    language: str,
    style: str = "formal",
    duration_seconds: Optional[int] = None,
    user_name: str = "",
    model: str = "",
    elapsed_ms: Optional[int] = None,
    status_code: int = 200,
    error: Optional[str] = None,
) -> Optional[str]:
    if not VOICE_LOG_ENABLED:
        return None
    try:
        _ensure_dirs()
        entry_id = _new_entry_id()
        entry = {
            "id": entry_id,
            "kind": "overview",
            "ts": _now_iso(),
            "user_id": _safe_user_id(user_id),
            "dataset_id": (dataset_id or "")[:64],
            "language": (language or "")[:32],
            "style": (style or "")[:32],
            "duration_seconds": duration_seconds,
            "user_name": (user_name or "")[:80],
            "model": (model or "")[:64],
            "output_chars": len(output_text or ""),
            "output_text": _truncate_text(output_text),
            "status": status_code,
            "duration_ms": elapsed_ms,
            "error": (error or None) if error else None,
        }
        _append_index(user_id, entry)
        return entry_id
    except Exception:
        return None


def log_audio_overview_session(
    *,
    user_id: str,
    dataset_id: str,
    output_text: str,
    language: str,
    voice: str,
    style: str = "formal",
    duration_seconds: Optional[int] = None,
    user_name: str = "",
    overview_model: str = "",
    tts_model: str = "",
    speaking_rate: Optional[float] = None,
    audio_bytes: Optional[bytes] = None,
    audio_format: str = "mp3",
    duration_ms: Optional[int] = None,
    status_code: int = 200,
    error: Optional[str] = None,
    extra: Optional[dict] = None,
) -> Optional[str]:
    """One Listen session: Gemini narration + TTS audio in a single log row."""
    if not VOICE_LOG_ENABLED:
        return None
    try:
        _ensure_dirs()
        _maybe_prune()
        entry_id = _new_entry_id()
        audio_rel_path = None
        audio_kb = 0
        file_ext = (audio_format or "mp3").strip().lower() or "mp3"
        if file_ext not in ("mp3", "wav", "ogg"):
            file_ext = "mp3"

        if audio_bytes and VOICE_LOG_KEEP_AUDIO:
            day = _today()
            day_dir = AUDIO_DIR / day
            day_dir.mkdir(parents=True, exist_ok=True)
            audio_file = day_dir / f"{_safe_user_id(user_id)}_{entry_id}.{file_ext}"
            try:
                audio_file.write_bytes(audio_bytes)
                audio_rel_path = f"audio/{day}/{audio_file.name}"
                audio_kb = len(audio_bytes) // 1024
            except Exception:
                audio_rel_path = None

        entry = {
            "id": entry_id,
            "kind": "audio_overview",
            "ts": _now_iso(),
            "user_id": _safe_user_id(user_id),
            "dataset_id": (dataset_id or "")[:64],
            "language": (language or "")[:32],
            "style": (style or "")[:32],
            "duration_seconds": duration_seconds,
            "user_name": (user_name or "")[:80],
            "model": (overview_model or "")[:64],
            "output_chars": len(output_text or ""),
            "output_text": _truncate_text(output_text),
            "voice": (voice or "")[:120],
            "speaking_rate": speaking_rate,
            "status": status_code,
            "duration_ms": duration_ms,
            "audio_bytes": len(audio_bytes) if audio_bytes else 0,
            "audio_kb": audio_kb,
            "audio_path": audio_rel_path,
            "error": (error or None) if error else None,
            "extra": {
                "tts_model": (tts_model or "")[:64],
                "audio_format": file_ext,
            },
        }
        if extra and isinstance(extra, dict):
            for k, v in list(extra.items())[:18]:
                entry["extra"][k] = v

        _append_index(user_id, entry)
        return entry_id
    except Exception:
        return None


def _append_index(user_id: str, entry: dict) -> None:
    path = _index_file(user_id)
    line = json.dumps(entry, ensure_ascii=False) + "\n"
    with _lock_for(user_id):
        with path.open("a", encoding="utf-8") as f:
            f.write(line)
        _trim_index(user_id)


def list_entries(
    user_id: str,
    *,
    kind: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
) -> list[dict]:
    """Return entries for the user, newest first."""
    if not VOICE_LOG_ENABLED:
        return []
    path = _index_file(user_id)
    if not path.exists():
        return []
    try:
        with _lock_for(user_id):
            with path.open("rb") as f:
                raw_lines = f.readlines()
    except Exception:
        return []

    parsed: list[dict] = []
    for raw in reversed(raw_lines):
        try:
            obj = json.loads(raw.decode("utf-8"))
        except Exception:
            continue
        entry_kind = obj.get("kind")
        if kind == "overview":
            if entry_kind not in ("overview", "audio_overview"):
                continue
        elif kind and entry_kind != kind:
            continue
        parsed.append(obj)

    return parsed[offset : offset + max(1, min(500, limit))]


def get_entry(user_id: str, entry_id: str) -> Optional[dict]:
    safe = _safe_entry_id(entry_id)
    if not safe:
        return None
    path = _index_file(user_id)
    if not path.exists():
        return None
    try:
        with _lock_for(user_id):
            with path.open("rb") as f:
                for raw in f:
                    try:
                        obj = json.loads(raw.decode("utf-8"))
                    except Exception:
                        continue
                    if obj.get("id") == safe:
                        return obj
    except Exception:
        return None
    return None


def read_audio_bytes(user_id: str, entry_id: str) -> Optional[bytes]:
    entry = get_entry(user_id, entry_id)
    if not entry:
        return None
    audio_rel = entry.get("audio_path")
    if not audio_rel:
        return None
    audio_path = VOICE_LOG_ROOT / audio_rel
    try:
        audio_path = audio_path.resolve()
        if not str(audio_path).startswith(str(VOICE_LOG_ROOT.resolve())):
            return None
        return audio_path.read_bytes()
    except Exception:
        return None


def delete_entry(user_id: str, entry_id: str) -> bool:
    """Remove one entry from the user's index and its audio file (if any)."""
    safe = _safe_entry_id(entry_id)
    if not safe:
        return False
    path = _index_file(user_id)
    if not path.exists():
        return False
    removed = False
    try:
        with _lock_for(user_id):
            with path.open("rb") as f:
                lines = f.readlines()
            keep = []
            for raw in lines:
                try:
                    obj = json.loads(raw.decode("utf-8"))
                except Exception:
                    keep.append(raw)
                    continue
                if obj.get("id") == safe:
                    removed = True
                    audio_rel = obj.get("audio_path")
                    if audio_rel:
                        try:
                            (VOICE_LOG_ROOT / audio_rel).unlink(missing_ok=True)
                        except Exception:
                            pass
                    continue
                keep.append(raw)
            tmp = path.with_suffix(".jsonl.tmp")
            with tmp.open("wb") as f:
                f.writelines(keep)
            os.replace(tmp, path)
    except Exception:
        return False
    return removed


def clear_user(user_id: str) -> int:
    """Wipe ALL entries (and their audio) for one user. Returns how many were removed."""
    path = _index_file(user_id)
    if not path.exists():
        return 0
    removed = 0
    try:
        with _lock_for(user_id):
            with path.open("rb") as f:
                lines = f.readlines()
            for raw in lines:
                try:
                    obj = json.loads(raw.decode("utf-8"))
                except Exception:
                    continue
                removed += 1
                audio_rel = obj.get("audio_path")
                if audio_rel:
                    try:
                        (VOICE_LOG_ROOT / audio_rel).unlink(missing_ok=True)
                    except Exception:
                        pass
            try:
                path.unlink()
            except Exception:
                path.write_bytes(b"")
    except Exception:
        return 0
    return removed


def usage_summary(user_id: str) -> dict:
    """Quick stats for the dashboard header (counts + chars + size)."""
    entries = list_entries(user_id, limit=500)
    tts = sum(1 for e in entries if e.get("kind") == "tts")
    translate = sum(1 for e in entries if e.get("kind") == "translate")
    overview = sum(1 for e in entries if e.get("kind") in ("overview", "audio_overview"))
    errors = sum(1 for e in entries if (e.get("status") or 200) >= 400 or e.get("error"))
    audio_kb = sum(int(e.get("audio_kb") or 0) for e in entries)
    chars_in = sum(int(e.get("input_chars") or 0) for e in entries)
    chars_out = sum(int(e.get("output_chars") or 0) for e in entries)
    return {
        "total": len(entries),
        "tts": tts,
        "translate": translate,
        "overview": overview,
        "errors": errors,
        "audio_kb": audio_kb,
        "input_chars": chars_in,
        "output_chars": chars_out,
        "max_entries": VOICE_LOG_MAX_ENTRIES_PER_USER,
        "audio_retention_days": VOICE_LOG_AUDIO_RETENTION_DAYS,
        "enabled": VOICE_LOG_ENABLED,
        "keep_audio": VOICE_LOG_KEEP_AUDIO,
    }
