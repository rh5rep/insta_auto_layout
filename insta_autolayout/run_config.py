from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class RunConfig:
    input: str | None = None
    output: str | None = None
    archive_output: str | None = None
    cache_dir: str | None = None
    count: int | None = None
    style: str | None = None
    duration_min: float | None = None
    duration_max: float | None = None
    scan_depth: str | None = None
    punchiness: str | None = None
    min_bpm: int | None = None
    audio_variants: str | None = None
    music_dir: str | None = None
    music_manifest: str | None = None
    seed: str | None = None
    manual_overrides: str | None = None
    diversity_strength: float | None = None
    text_overlays: dict[str, Any] | None = None
    brand_cards: dict[str, Any] | None = None
    batches: list[dict[str, Any]] | None = None


def load_run_config(path: str | None) -> RunConfig:
    if not path:
        return RunConfig()
    config_path = Path(path).expanduser().resolve()
    if not config_path.exists():
        raise SystemExit(f"Config file does not exist: {config_path}")
    raw = json.loads(config_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise SystemExit(f"Config file must contain a JSON object: {config_path}")
    return RunConfig(
        input=_optional_str(raw.get("input")),
        output=_optional_str(raw.get("output")),
        archive_output=_optional_str(raw.get("archive_output", raw.get("archive-output"))),
        cache_dir=_optional_str(raw.get("cache_dir", raw.get("cache-dir"))),
        count=_optional_int(raw.get("count")),
        style=_optional_str(raw.get("style")),
        duration_min=_optional_float(raw.get("duration_min", raw.get("duration-min"))),
        duration_max=_optional_float(raw.get("duration_max", raw.get("duration-max"))),
        scan_depth=_optional_str(raw.get("scan_depth", raw.get("scan-depth"))),
        punchiness=_optional_str(raw.get("punchiness")),
        min_bpm=_optional_int(raw.get("min_bpm", raw.get("min-bpm"))),
        audio_variants=_variants(raw.get("audio_variants", raw.get("audio-variants"))),
        music_dir=_optional_str(raw.get("music_dir", raw.get("music-dir"))),
        music_manifest=_optional_str(raw.get("music_manifest", raw.get("music-manifest"))),
        seed=_optional_str(raw.get("seed")),
        manual_overrides=_optional_str(raw.get("manual_overrides", raw.get("manual-overrides"))),
        diversity_strength=_optional_float(raw.get("diversity_strength", raw.get("diversity-strength"))),
        text_overlays=(
            raw.get("text_overlays", raw.get("text-overlays"))
            if isinstance(raw.get("text_overlays", raw.get("text-overlays")), dict)
            else None
        ),
        brand_cards=(
            raw.get("brand_cards", raw.get("brand-cards"))
            if isinstance(raw.get("brand_cards", raw.get("brand-cards")), dict)
            else None
        ),
        batches=raw.get("batches") if isinstance(raw.get("batches"), list) else None,
    )


def config_value(cli_value: Any, config_value_: Any, default: Any = None) -> Any:
    return cli_value if cli_value is not None else config_value_ if config_value_ is not None else default


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_int(value: object) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _optional_float(value: object) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _variants(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, list):
        return ",".join(str(item).strip() for item in value if str(item).strip())
    return _optional_str(value)
