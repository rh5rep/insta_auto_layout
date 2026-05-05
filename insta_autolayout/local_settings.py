from __future__ import annotations

import json
from pathlib import Path
from typing import Any


SETTINGS_DIR = Path.home() / ".insta_autolayout"
SETTINGS_PATH = SETTINGS_DIR / "local_settings.json"


def default_settings() -> dict[str, str]:
    base_dir = SETTINGS_DIR
    return {
        "reviewer_id": "rami",
        "project_id": "trybe",
        "preset_id": "",
        "input_dir": "",
        "output_dir": "",
        "archive_dir": "",
        "shared_state_dir": "",
        "cache_dir": "",
        "config_path": "",
        "latest_batch_dir": "",
        "count": "",
        "duration_min": "",
        "duration_max": "",
        "style": "",
        "scan_depth": "",
        "punchiness": "",
        "min_bpm": "",
        "diversity_strength": "",
        "audio_variants": "",
        "music_dir": "",
        "music_manifest": "",
        "seed": "",
    }


def load_local_settings() -> dict[str, str]:
    settings = default_settings()
    if not SETTINGS_PATH.exists():
        return settings
    try:
        raw = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return settings
    if not isinstance(raw, dict):
        return settings
    for key in settings:
        value = raw.get(key)
        if value is not None:
            settings[key] = str(value)
    return settings


def save_local_settings(settings: dict[str, Any]) -> dict[str, str]:
    merged = default_settings()
    for key in merged:
        value = settings.get(key)
        if value is not None:
            merged[key] = str(value)
    SETTINGS_DIR.mkdir(parents=True, exist_ok=True)
    SETTINGS_PATH.write_text(json.dumps(merged, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return merged
