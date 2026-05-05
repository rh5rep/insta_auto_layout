from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_manual_overrides(input_dir: Path, override_path: str | None) -> dict[str, Any]:
    path = Path(override_path) if override_path else input_dir / "manual-overrides.json"
    if not path.exists():
        return {
            "pin_hero": None,
            "exclude_files": set(),
            "prefer_files": set(),
            "avoid_files": set(),
            "clip_ratings": {},
            "force_order": [],
            "force_collage": [],
        }

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
        "path": str(path),
    }


def asset_matches_override(asset_path: str, file_name: str, token: str | None) -> bool:
    if not token:
        return False
    return token in {asset_path, file_name, Path(asset_path).name}


def _normalize_name(value: str | None) -> str | None:
    if value is None:
        return None
    return value.replace("\\", "/")
