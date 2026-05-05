from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .promo_models import ClipCandidate
from .scanner import IMAGE_EXTENSIONS, VIDEO_EXTENSIONS, ScanOutcome
from .models import MediaAsset


SCAN_CACHE_VERSION = 1
CANDIDATE_CACHE_VERSION = 1


def default_cache_dir(input_dir: Path) -> Path:
    return input_dir / ".insta_autolayout_cache"


def compute_input_signature(input_dir: Path) -> str:
    entries: list[dict[str, object]] = []
    for path in sorted(input_dir.rglob("*")):
        if not path.is_file():
            continue
        if path.name.startswith("."):
            continue
        stat = path.stat()
        entries.append(
            {
                "path": str(path.relative_to(input_dir)),
                "size": stat.st_size,
                "mtime_ns": stat.st_mtime_ns,
                "suffix": path.suffix.lower(),
            }
        )
    payload = {"entries": entries}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def compute_asset_state_signature(assets: list[MediaAsset]) -> str:
    payload = [asset.to_dict() for asset in assets]
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def load_scan_outcome(cache_dir: Path, input_signature: str) -> ScanOutcome | None:
    path = cache_dir / "scan_cache.json"
    if not path.exists():
        return None
    raw = json.loads(path.read_text(encoding="utf-8"))
    if raw.get("version") != SCAN_CACHE_VERSION or raw.get("input_signature") != input_signature:
        return None
    assets = [_asset_from_dict(item) for item in raw.get("assets", []) if isinstance(item, dict)]
    exclusions = [item for item in raw.get("exclusions", []) if isinstance(item, dict)]
    return ScanOutcome(assets=assets, exclusions=exclusions)


def save_scan_outcome(cache_dir: Path, input_signature: str, outcome: ScanOutcome) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / "scan_cache.json"
    payload = {
        "version": SCAN_CACHE_VERSION,
        "input_signature": input_signature,
        "assets": [asset.to_dict() for asset in outcome.assets],
        "exclusions": outcome.exclusions,
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def load_candidates(cache_dir: Path, asset_state_signature: str, style: str, scan_depth: str) -> list[ClipCandidate] | None:
    path = cache_dir / f"candidates_{style}_{scan_depth}.json"
    if not path.exists():
        return None
    raw = json.loads(path.read_text(encoding="utf-8"))
    if raw.get("version") != CANDIDATE_CACHE_VERSION or raw.get("asset_state_signature") != asset_state_signature:
        return None
    return [_candidate_from_dict(item) for item in raw.get("candidates", []) if isinstance(item, dict)]


def save_candidates(cache_dir: Path, asset_state_signature: str, style: str, scan_depth: str, candidates: list[ClipCandidate]) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / f"candidates_{style}_{scan_depth}.json"
    payload = {
        "version": CANDIDATE_CACHE_VERSION,
        "asset_state_signature": asset_state_signature,
        "style": style,
        "scan_depth": scan_depth,
        "candidates": [candidate.to_dict() for candidate in candidates],
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def write_manifest(cache_dir: Path, input_dir: Path, input_signature: str, asset_state_signature: str, style: str, scan_depth: str) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    supported_suffixes = sorted(IMAGE_EXTENSIONS | VIDEO_EXTENSIONS)
    payload = {
        "input_dir": str(input_dir),
        "input_signature": input_signature,
        "asset_state_signature": asset_state_signature,
        "style": style,
        "scan_depth": scan_depth,
        "supported_suffixes": supported_suffixes,
    }
    (cache_dir / "manifest.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _asset_from_dict(raw: dict) -> MediaAsset:
    return MediaAsset(
        source_path=str(raw["source_path"]),
        file_name=str(raw["file_name"]),
        media_type=str(raw["media_type"]),
        width=int(raw["width"]),
        height=int(raw["height"]),
        duration=float(raw["duration"]) if raw.get("duration") is not None else None,
        aspect_ratio=float(raw["aspect_ratio"]),
        orientation=str(raw["orientation"]),
        sharpness=float(raw["sharpness"]),
        average_hash=str(raw["average_hash"]),
        difference_hash=str(raw["difference_hash"]),
        mean_color=tuple(float(value) for value in raw.get("mean_color", (0.0, 0.0, 0.0))),
        face_boxes=list(raw.get("face_boxes", [])),
        salient_box=raw.get("salient_box"),
        edge_risk=float(raw.get("edge_risk", 0.0)),
        duplicate_score=float(raw.get("duplicate_score", 0.0)),
        duplicate_group=raw.get("duplicate_group"),
        duplicate_representative=bool(raw.get("duplicate_representative", True)),
        excluded_reason=raw.get("excluded_reason"),
        analysis_notes=list(raw.get("analysis_notes", [])),
        hero_score=float(raw.get("hero_score", 0.0)),
        slide_score=float(raw.get("slide_score", 0.0)),
    )


def _candidate_from_dict(raw: dict) -> ClipCandidate:
    return ClipCandidate(
        candidate_id=str(raw["candidate_id"]),
        source_file=str(raw["source_file"]),
        source_type=str(raw["source_type"]),
        source_start=float(raw["source_start"]),
        source_end=float(raw["source_end"]),
        playback_rate=float(raw["playback_rate"]),
        base_duration=float(raw["base_duration"]),
        width=int(raw["width"]),
        height=int(raw["height"]),
        orientation=str(raw["orientation"]),
        face_count=int(raw["face_count"]),
        edge_risk=float(raw["edge_risk"]),
        sharpness=float(raw["sharpness"]),
        motion_energy=float(raw["motion_energy"]),
        boundary_confidence=float(raw["boundary_confidence"]),
        score_total=float(raw["score_total"]),
        score_breakdown={str(key): float(value) for key, value in dict(raw.get("score_breakdown", {})).items()},
        crop_strategy=str(raw["crop_strategy"]),
        visual_role=str(raw["visual_role"]),
        tags=[str(item) for item in raw.get("tags", [])],
        why_candidate=str(raw["why_candidate"]),
    )
