"""Filter Gemini Live user transcripts that arrive in the wrong script/language."""

from __future__ import annotations

_NEUTRAL = set(".,!?;:'\"()[]{}_\\/@#$%&*+=<>-")


def _script_counts(text: str) -> dict[str, int]:
    arabic = latin = devanagari = cyrillic = other = 0
    for ch in text:
        cp = ord(ch)
        if (
            0x0600 <= cp <= 0x06FF
            or 0x0750 <= cp <= 0x077F
            or 0x08A0 <= cp <= 0x08FF
            or 0xFB50 <= cp <= 0xFDFF
            or 0xFE70 <= cp <= 0xFEFF
        ):
            arabic += 1
        elif (65 <= cp <= 90) or (97 <= cp <= 122):
            latin += 1
        elif 0x0900 <= cp <= 0x097F:
            devanagari += 1
        elif 0x0400 <= cp <= 0x04FF:
            cyrillic += 1
        elif ch.isspace() or ch.isdigit() or ch in _NEUTRAL:
            continue
        else:
            other += 1
    return {
        "arabic": arabic,
        "latin": latin,
        "devanagari": devanagari,
        "cyrillic": cyrillic,
        "other": other,
    }


def user_transcript_allowed(text: str, lang: str) -> bool:
    """Return False when Live STT returns the wrong script for the session language."""
    t = (text or "").strip()
    if not t:
        return False

    counts = _script_counts(t)
    letters = sum(counts[k] for k in ("arabic", "latin", "devanagari", "cyrillic", "other"))
    if letters == 0:
        return True

    if counts["devanagari"] > 0 or counts["cyrillic"] > 0:
        return False

    normalized = (lang or "en-US").strip().lower()
    if normalized.startswith("ar"):
        if counts["arabic"] == 0 and counts["latin"] > 0:
            return False
        return counts["arabic"] > 0 or counts["latin"] <= counts["arabic"] * 2

    if normalized.startswith("en"):
        if counts["latin"] == 0 and counts["arabic"] > 0:
            return False
        return counts["latin"] > 0 or counts["arabic"] <= counts["latin"]

    return True
