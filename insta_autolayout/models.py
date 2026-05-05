from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


ASPECT_RATIOS: dict[str, tuple[int, int]] = {
    "1:1": (1, 1),
    "4:5": (4, 5),
    "3:4": (3, 4),
    "9:16": (9, 16),
}

CANVAS_SIZES: dict[str, tuple[int, int]] = {
    "1:1": (1080, 1080),
    "4:5": (1080, 1350),
    "3:4": (1080, 1440),
    "9:16": (1080, 1920),
}


@dataclass(slots=True)
class MediaAsset:
    source_path: str
    file_name: str
    media_type: str
    width: int
    height: int
    duration: float | None
    aspect_ratio: float
    orientation: str
    sharpness: float
    average_hash: str
    difference_hash: str
    mean_color: tuple[float, float, float] = (0.0, 0.0, 0.0)
    face_boxes: list[dict[str, float]] = field(default_factory=list)
    salient_box: dict[str, float] | None = None
    edge_risk: float = 0.0
    duplicate_score: float = 0.0
    duplicate_group: str | None = None
    duplicate_representative: bool = True
    excluded_reason: str | None = None
    analysis_notes: list[str] = field(default_factory=list)
    hero_score: float = 0.0
    slide_score: float = 0.0

    @property
    def source(self) -> Path:
        return Path(self.source_path)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class SlidePlan:
    index: int
    kind: str
    source_files: list[str]
    crop_strategy: str
    why_chosen: str
    export_path: str
    layout_template: str | None = None
    needs_manual_review: bool = False
    review_reason: str | None = None
    duration: float | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        if not self.layout_template:
            data.pop("layout_template")
        if not self.review_reason:
            data.pop("review_reason")
        if self.duration is None:
            data.pop("duration")
        if not self.extra:
            data.pop("extra")
        return data


@dataclass(slots=True)
class Plan:
    chosen_mode: str
    target_aspect_ratio: str
    recommended_caption_stub: str
    slides: list[SlidePlan]
    selected_assets: list[str]
    excluded_assets: list[dict[str, str]]
    review_slides: list[int]
    output_canvas: tuple[int, int]
    primary_export: str | None = None
    variant_key: str | None = None
    variant_label: str | None = None
    variant_rank: int | None = None
    variant_score: float | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["slides"] = [slide.to_dict() for slide in self.slides]
        return data


def canvas_size_for(target_aspect: str) -> tuple[int, int]:
    if target_aspect not in CANVAS_SIZES:
        raise ValueError(f"Unsupported target aspect ratio: {target_aspect}")
    return CANVAS_SIZES[target_aspect]


def normalize_aspect(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    if value not in ASPECT_RATIOS:
        raise ValueError(f"Unsupported aspect ratio: {value}")
    return value
