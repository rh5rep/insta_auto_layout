from __future__ import annotations

from pathlib import Path
from typing import Any


POSITIVE_SOURCE_TAGS = {"source_high_quality", "postworthy", "strong_hook", "good_action"}
NEGATIVE_SOURCE_TAGS = {"source_overused", "off_brand", "weak_hook", "too_shaky", "too_slow"}
POSITIVE_CLIP_TAGS = {"good_trim", "good_crop", "good_pacing", "strong_hook", "good_action"}
NEGATIVE_CLIP_TAGS = {
    "bad_trim",
    "bad_crop",
    "bad_pacing",
    "starts_too_early",
    "starts_too_late",
    "ends_too_early",
    "ends_too_late",
    "too_shaky",
    "too_slow",
    "repetitive",
}


def derive_manual_overrides(events: list[dict[str, Any]]) -> dict[str, Any]:
    prefer_files: set[str] = set()
    avoid_files: set[str] = set()
    clip_ratings: dict[str, float] = {}

    for event in events:
        target = dict(event.get("target") or {})
        target_type = str(target.get("type") or "")
        tags = {str(tag) for tag in event.get("reason_tags") or [] if tag}
        status = str(event.get("status") or "")
        rating = _numeric_rating(event.get("rating"))

        if target_type == "source_file":
            source_file = _source_file_token(target)
            if not source_file:
                continue
            direction = _source_direction(status, tags, rating)
            if direction > 0:
                prefer_files.add(source_file)
                avoid_files.discard(source_file)
            elif direction < 0:
                avoid_files.add(source_file)
                prefer_files.discard(source_file)
        elif target_type == "clip":
            clip_token = _clip_token(target)
            if not clip_token:
                continue
            delta = _clip_delta(status, tags, rating)
            if delta:
                clip_ratings[clip_token] = _clamp_rating(clip_ratings.get(clip_token, 0.0) + delta)

    return {
        "prefer_files": sorted(prefer_files),
        "avoid_files": sorted(avoid_files),
        "clip_ratings": {key: _clean_number(value) for key, value in sorted(clip_ratings.items()) if value},
    }


def _source_direction(status: str, tags: set[str], rating: float | None) -> int:
    if status in {"approved", "shortlist"} or tags & POSITIVE_SOURCE_TAGS:
        return 1
    if status == "reject" or tags & NEGATIVE_SOURCE_TAGS:
        return -1
    if status == "needs_edit" and rating is not None and rating < 0:
        return -1
    if rating is not None:
        return 1 if rating > 0 else -1 if rating < 0 else 0
    return 0


def _clip_delta(status: str, tags: set[str], rating: float | None) -> float:
    if rating is not None:
        return max(-3.0, min(3.0, rating))
    if status in {"approved", "shortlist"} or tags & POSITIVE_CLIP_TAGS:
        return 1.0
    if status == "reject" or tags & NEGATIVE_CLIP_TAGS:
        return -1.0
    if status == "needs_edit":
        return -1.0
    return 0.0


def _source_file_token(target: dict[str, Any]) -> str | None:
    value = target.get("source_file") or target.get("file") or target.get("path")
    return _normalize_name(value)


def _clip_token(target: dict[str, Any]) -> str | None:
    explicit = target.get("clip_token") or target.get("candidate_id")
    if explicit:
        return _normalize_name(explicit)
    source_file = _source_file_token(target)
    if not source_file:
        return None
    source_start = target.get("source_start")
    source_end = target.get("source_end")
    if source_start is None:
        return source_file
    if source_end is None:
        source_end = source_start
    return f"{source_file}@{_format_time(source_start)}-{_format_time(source_end)}"


def _numeric_rating(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _clamp_rating(value: float) -> float:
    return max(-3.0, min(3.0, value))


def _clean_number(value: float) -> int | float:
    rounded = round(value, 2)
    if rounded == int(rounded):
        return int(rounded)
    return rounded


def _format_time(value: Any) -> str:
    number = float(value)
    text = f"{number:.2f}".rstrip("0").rstrip(".")
    return text or "0"


def _normalize_name(value: Any) -> str | None:
    if value is None:
        return None
    return str(value).replace("\\", "/")
