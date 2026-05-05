from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


PROMO_CANVAS = (1080, 1920)


@dataclass(slots=True)
class ClipScores:
    technical_quality: float
    subject_strength: float
    vertical_fit: float
    editability: float
    motion_energy: float
    boundary_confidence: float
    style_fit: float
    novelty: float

    @property
    def total(self) -> float:
        return (
            0.20 * self.technical_quality
            + 0.18 * self.subject_strength
            + 0.15 * self.vertical_fit
            + 0.13 * self.editability
            + 0.14 * self.motion_energy
            + 0.09 * self.boundary_confidence
            + 0.10 * self.style_fit
            + 0.01 * self.novelty
        )

    def to_dict(self) -> dict[str, float]:
        data = asdict(self)
        data["total"] = round(self.total, 4)
        return {key: round(value, 4) for key, value in data.items()}


@dataclass(slots=True)
class ClipCandidate:
    candidate_id: str
    source_file: str
    source_type: str
    source_start: float
    source_end: float
    playback_rate: float
    base_duration: float
    width: int
    height: int
    orientation: str
    face_count: int
    edge_risk: float
    sharpness: float
    motion_energy: float
    boundary_confidence: float
    score_total: float
    score_breakdown: dict[str, float]
    crop_strategy: str
    visual_role: str
    tags: list[str]
    why_candidate: str

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["score_total"] = round(self.score_total, 4)
        data["source_start"] = round(self.source_start, 3)
        data["source_end"] = round(self.source_end, 3)
        data["playback_rate"] = round(self.playback_rate, 3)
        data["base_duration"] = round(self.base_duration, 3)
        return data


@dataclass(slots=True)
class TimelineItem:
    candidate_id: str
    source_file: str
    source_type: str
    source_start: float
    source_end: float
    timeline_start: float
    timeline_end: float
    playback_rate: float
    duration: float
    transition_in: str
    transition_out: str
    crop_strategy: str
    motion_treatment: str
    score_total: float
    why_selected: str
    score_breakdown: dict[str, float]

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        for key in ("source_start", "source_end", "timeline_start", "timeline_end", "playback_rate", "duration", "score_total"):
            data[key] = round(float(data[key]), 4)
        return data


@dataclass(slots=True)
class VideoConcept:
    concept_id: str
    style: str
    strategy: str
    target_duration: float
    why_this_version: str
    diversity_notes: list[str]
    timeline: list[TimelineItem]
    used_candidate_ids: list[str]
    used_source_files: list[str]

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["timeline"] = [item.to_dict() for item in self.timeline]
        data["target_duration"] = round(self.target_duration, 4)
        return data


@dataclass(slots=True)
class AudioVariant:
    variant_id: str
    audio_mode: str
    bpm: int | None
    has_audio: bool
    display_name: str
    render_name: str
    timeline: list[TimelineItem]
    track_label: str | None = None
    track_title: str | None = None
    track_source: str | None = None
    track_license: str | None = None
    track_credit: str | None = None
    track_original_path: str | None = None
    track_download_url: str | None = None
    track_page_url: str | None = None
    track_start_sec: float = 0.0
    selection_reason: str | None = None
    report_notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "variant_id": self.variant_id,
            "audio_mode": self.audio_mode,
            "bpm": self.bpm,
            "has_audio": self.has_audio,
            "display_name": self.display_name,
            "render_name": self.render_name,
            "track_label": self.track_label,
            "track_title": self.track_title,
            "track_source": self.track_source,
            "track_license": self.track_license,
            "track_credit": self.track_credit,
            "track_original_path": self.track_original_path,
            "track_download_url": self.track_download_url,
            "track_page_url": self.track_page_url,
            "track_start_sec": round(self.track_start_sec, 4),
            "selection_reason": self.selection_reason,
            "report_notes": self.report_notes,
            "timeline": [item.to_dict() for item in self.timeline],
        }


@dataclass(slots=True)
class PromoOutput:
    concept: VideoConcept
    variants: list[AudioVariant]
    report: dict[str, Any]


def clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))
