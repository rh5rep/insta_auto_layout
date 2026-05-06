from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_manual_overrides(
    input_dir: Path,
    override_path: str | None,
    shared_state_dir: str | Path | None = None,
) -> dict[str, Any]:
    merged = _empty_overrides()
    applied_paths: list[str] = []
    derived_path = _derived_overrides_path(shared_state_dir)
    manual_path = Path(override_path).expanduser().resolve() if override_path else (input_dir / "manual-overrides.json").resolve()

    if derived_path and derived_path.exists():
        merged = _merge_overrides(merged, _load_override_file(derived_path), prefer_overlay=True)
        applied_paths.append(str(derived_path))

    if manual_path.exists() and (derived_path is None or manual_path != derived_path):
        merged = _merge_overrides(merged, _load_override_file(manual_path), prefer_overlay=True)
        applied_paths.append(str(manual_path))

    if manual_path.exists():
        merged["manual_path"] = str(manual_path)
    if derived_path and derived_path.exists():
        merged["derived_path"] = str(derived_path)
        merged["using_generated_feedback"] = True
    if applied_paths:
        merged["paths"] = applied_paths
        merged["path"] = applied_paths[-1]
    return merged


def _empty_overrides() -> dict[str, Any]:
    return {
        "pin_hero": None,
        "exclude_files": set(),
        "prefer_files": set(),
        "avoid_files": set(),
        "clip_ratings": {},
        "force_order": [],
        "force_collage": [],
    }


def _load_override_file(path: Path) -> dict[str, Any]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return {
        "pin_hero": _normalize_name(raw.get("pin_hero")),
        "exclude_files": {_normalize_name(item) for item in raw.get("exclude_files", []) if item},
        "prefer_files": {_normalize_name(item) for item in raw.get("prefer_files", []) if item},
        "avoid_files": {_normalize_name(item) for item in raw.get("avoid_files", []) if item},
        "clip_ratings": {
            _normalize_name(str(key)): float(value)
            for key, value in raw.get("clip_ratings", {}).items()
            if key and isinstance(value, (int, float))
        },
        "force_order": [_normalize_name(item) for item in raw.get("force_order", []) if item],
        "force_collage": [
            [_normalize_name(item) for item in group if item]
            for group in raw.get("force_collage", [])
            if group
        ],
    }


def _merge_overrides(base: dict[str, Any], overlay: dict[str, Any], prefer_overlay: bool) -> dict[str, Any]:
    merged = {
        "pin_hero": overlay.get("pin_hero") if prefer_overlay and overlay.get("pin_hero") else base.get("pin_hero"),
        "exclude_files": set(base.get("exclude_files", set())) | set(overlay.get("exclude_files", set())),
        "prefer_files": set(base.get("prefer_files", set())),
        "avoid_files": set(base.get("avoid_files", set())),
        "clip_ratings": dict(base.get("clip_ratings", {})),
        "force_order": list(base.get("force_order", [])),
        "force_collage": list(base.get("force_collage", [])),
    }

    for token in overlay.get("prefer_files", set()):
        merged["prefer_files"].add(token)
        merged["avoid_files"].discard(token)
    for token in overlay.get("avoid_files", set()):
        merged["avoid_files"].add(token)
        merged["prefer_files"].discard(token)

    for token, rating in overlay.get("clip_ratings", {}).items():
        if prefer_overlay or token not in merged["clip_ratings"]:
            merged["clip_ratings"][token] = rating

    if prefer_overlay and overlay.get("force_order"):
        merged["force_order"] = list(overlay["force_order"])
    if prefer_overlay and overlay.get("force_collage"):
        merged["force_collage"] = list(overlay["force_collage"])
    return merged


def _derived_overrides_path(shared_state_dir: str | Path | None) -> Path | None:
    if not shared_state_dir:
        return None
    root = Path(shared_state_dir).expanduser()
    review_state_dir = root if root.name == "review_state" else root / "review_state"
    return review_state_dir / "derived" / "manual-overrides.generated.json"


def asset_matches_override(asset_path: str, file_name: str, token: str | None) -> bool:
    if not token:
        return False
    return token in {asset_path, file_name, Path(asset_path).name}


def _normalize_name(value: str | None) -> str | None:
    if value is None:
        return None
    return value.replace("\\", "/")
