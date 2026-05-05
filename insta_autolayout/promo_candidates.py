from __future__ import annotations

import math
from pathlib import Path

import numpy as np

from .models import MediaAsset
from .overrides import asset_matches_override
from .moviepy_compat import VideoFileClip
from .promo_models import ClipCandidate, ClipScores, clamp01
from .rendering import estimate_sharpness


class PromoCandidateBuilder:
    def __init__(self, scan_depth: str = "balanced") -> None:
        self.scan_depth = scan_depth if scan_depth in {"quick", "balanced", "deep"} else "balanced"

    def build(self, assets: list[MediaAsset], style: str) -> list[ClipCandidate]:
        candidates: list[ClipCandidate] = []
        for asset in assets:
            if asset.media_type == "image":
                candidates.extend(self._image_candidates(asset, style))
            else:
                candidates.extend(self._video_candidates(asset, style))
        return sorted(candidates, key=lambda candidate: (-candidate.score_total, candidate.source_file, candidate.candidate_id))

    def _image_candidates(self, asset: MediaAsset, style: str) -> list[ClipCandidate]:
        vertical_fit = _vertical_fit(asset.width, asset.height)
        technical = _sharpness_quality(asset.sharpness)
        subject = _subject_strength(asset.face_boxes, asset.edge_risk)
        editability = clamp01(0.60 + (0.20 * vertical_fit) + (0.20 * (1 - asset.edge_risk)))
        motion = 0.18 + min(len(asset.face_boxes), 4) * 0.07
        style_fit = _style_fit(style, motion_energy=motion, face_count=len(asset.face_boxes), is_video=False)
        novelty = clamp01(1.0 - min(asset.duplicate_score, 1.0))
        scores = ClipScores(
            technical_quality=technical,
            subject_strength=subject,
            vertical_fit=vertical_fit,
            editability=editability,
            motion_energy=motion,
            boundary_confidence=0.82,
            style_fit=style_fit,
            novelty=novelty,
        )
        if style == "fast_punchy":
            base_duration = 0.42 + (0.28 * (1 - motion))
        else:
            base_duration = 0.9 + (0.6 * (1 - motion))
        crop_strategy = "smart_crop" if asset.orientation != "landscape" or asset.edge_risk < 0.55 else "pad"
        role = _visual_role(asset.media_type, len(asset.face_boxes), motion)
        why = f"strong still frame with {len(asset.face_boxes)} faces and vertical fit {vertical_fit:.2f}"
        return [
            ClipCandidate(
                candidate_id=f"{Path(asset.file_name).stem}_img",
                source_file=asset.source_path,
                source_type="image",
                source_start=0.0,
                source_end=0.0,
                playback_rate=1.0,
                base_duration=round(base_duration, 3),
                width=asset.width,
                height=asset.height,
                orientation=asset.orientation,
                face_count=len(asset.face_boxes),
                edge_risk=asset.edge_risk,
                sharpness=asset.sharpness,
                motion_energy=motion,
                boundary_confidence=0.82,
                score_total=scores.total,
                score_breakdown=scores.to_dict(),
                crop_strategy=crop_strategy,
                visual_role=role,
                tags=_candidate_tags(asset.media_type, len(asset.face_boxes), motion, asset.orientation),
                why_candidate=why,
            )
        ]

    def _video_candidates(self, asset: MediaAsset, style: str) -> list[ClipCandidate]:
        if VideoFileClip is None or not asset.duration:
            return []

        window_specs = _window_specs(asset.duration, style, self.scan_depth)
        candidates: list[ClipCandidate] = []
        with VideoFileClip(asset.source_path, audio=False) as clip:
            for index, (start, end) in enumerate(window_specs, start=1):
                if self.scan_depth == "quick":
                    refined_start, refined_end = start, end
                    boundary_confidence = 0.64
                    motion = _sample_motion(clip, refined_start, refined_end)
                    sharpness = asset.sharpness
                else:
                    refined_start, refined_end, boundary_confidence, motion = _refine_window(clip, start, end, style, self.scan_depth)
                    sharpness = _sample_sharpness(clip, refined_start, refined_end)
                playback_rate = _recommended_speed(motion, end - start, style)
                duration = max(0.35, (refined_end - refined_start) / playback_rate)
                minimum_duration = 0.38 if style == "fast_punchy" else 0.85
                if duration < minimum_duration:
                    continue
                vertical_fit = _vertical_fit(asset.width, asset.height)
                technical = _sharpness_quality((asset.sharpness + sharpness) / 2)
                subject = _subject_strength(asset.face_boxes, asset.edge_risk) + min(0.12, motion * 0.15)
                editability = clamp01(
                    0.42 + (0.18 * vertical_fit) + (0.14 * (1 - asset.edge_risk)) + (0.12 * motion) + (0.14 * boundary_confidence)
                )
                style_fit = _style_fit(style, motion_energy=motion, face_count=len(asset.face_boxes), is_video=True)
                novelty = clamp01(1.0 - min(asset.duplicate_score, 1.0))
                scores = ClipScores(
                    technical_quality=technical,
                    subject_strength=clamp01(subject),
                    vertical_fit=vertical_fit,
                    editability=editability,
                    motion_energy=clamp01(motion),
                    boundary_confidence=boundary_confidence,
                    style_fit=style_fit,
                    novelty=novelty,
                )
                crop_strategy = "smart_crop" if vertical_fit > 0.55 else "smart_crop_or_pad"
                role = _visual_role(asset.media_type, len(asset.face_boxes), motion)
                why = (
                    f"usable motion window {refined_start:.1f}-{refined_end:.1f}s "
                    f"with energy {motion:.2f}, boundary confidence {boundary_confidence:.2f}, at {playback_rate:.2f}x"
                )
                candidates.append(
                    ClipCandidate(
                        candidate_id=f"{Path(asset.file_name).stem}_vid_{index:02d}",
                        source_file=asset.source_path,
                        source_type="video",
                        source_start=round(refined_start, 3),
                        source_end=round(refined_end, 3),
                        playback_rate=round(playback_rate, 3),
                        base_duration=round(duration, 3),
                        width=asset.width,
                        height=asset.height,
                        orientation=asset.orientation,
                        face_count=len(asset.face_boxes),
                        edge_risk=asset.edge_risk,
                        sharpness=sharpness,
                        motion_energy=motion,
                        boundary_confidence=boundary_confidence,
                        score_total=scores.total,
                        score_breakdown=scores.to_dict(),
                        crop_strategy=crop_strategy,
                        visual_role=role,
                        tags=_candidate_tags(asset.media_type, len(asset.face_boxes), motion, asset.orientation),
                        why_candidate=why,
                    )
                )
        return candidates


def _window_specs(duration: float, style: str, scan_depth: str = "balanced") -> list[tuple[float, float]]:
    if style == "fast_punchy":
        if scan_depth == "quick":
            if duration <= 8:
                count = 2
                window = min(0.85, duration)
            elif duration <= 20:
                count = 3
                window = 0.90
            elif duration <= 60:
                count = 4
                window = 0.95
            elif duration <= 120:
                count = 5
                window = 1.00
            else:
                count = 6
                window = 1.05
            if duration <= window:
                return [(0.0, duration)]
            starts = np.linspace(0.0, max(duration - window, 0.0), num=count)
            return _dedupe_windows([(float(start), float(start + window)) for start in starts], threshold=0.16)

        if duration <= 8:
            count = 3
            window = min(0.85, duration)
        elif duration <= 20:
            count = 5
            window = 0.90
        elif duration <= 60:
            count = 6
            window = 0.95
        elif duration <= 120:
            count = 7
            window = 1.00
        else:
            count = 8
            window = 1.05

        if duration <= window:
            return [(0.0, duration)]
        starts = np.linspace(0.0, max(duration - window, 0.0), num=count)
        return _dedupe_windows([(float(start), float(start + window)) for start in starts], threshold=0.12)

    if duration <= 8:
        fractions = [0.15, 0.55]
        window = min(2.0, duration)
    elif duration <= 30:
        fractions = [0.12, 0.35, 0.58, 0.82]
        window = 2.2
    elif duration <= 120:
        fractions = [0.08, 0.24, 0.42, 0.58, 0.76, 0.9]
        window = 2.6
    else:
        fractions = [0.06, 0.18, 0.34, 0.5, 0.66, 0.82, 0.94]
        window = 2.8

    specs = []
    for frac in fractions:
        center = duration * frac
        start = max(0.0, center - (window / 2))
        end = min(duration, start + window)
        start = max(0.0, end - window)
        specs.append((start, end))
    return _dedupe_windows(specs)


def _dedupe_windows(windows: list[tuple[float, float]], threshold: float = 0.55) -> list[tuple[float, float]]:
    deduped: list[tuple[float, float]] = []
    for start, end in windows:
        if deduped and abs(start - deduped[-1][0]) < threshold:
            continue
        deduped.append((start, end))
    return deduped


def _sample_motion(clip, start: float, end: float) -> float:
    if end - start <= 0.15:
        return 0.0
    safe_end = max(start + 0.05, end - 0.03)
    sample_times = np.linspace(start, safe_end, num=4)
    frames = [clip.get_frame(float(time)) for time in sample_times]
    deltas = []
    for current, following in zip(frames, frames[1:], strict=False):
        current_small = _small_gray(current)
        following_small = _small_gray(following)
        deltas.append(float(np.mean(np.abs(current_small.astype(np.float32) - following_small.astype(np.float32))) / 255.0))
    return clamp01((sum(deltas) / max(len(deltas), 1)) * 4.5)


def _sample_sharpness(clip, start: float, end: float) -> float:
    midpoint = min(start + ((end - start) / 2), max(start + 0.01, end - 0.03))
    frame = clip.get_frame(float(midpoint))
    return estimate_sharpness(frame.astype(np.uint8))


def _refine_window(clip, start: float, end: float, style: str, scan_depth: str = "balanced") -> tuple[float, float, float, float]:
    base_length = end - start
    # Fast-punchy batches can touch many windows per video, so keep the local
    # trim search intentionally small. This preserves boundary refinement
    # without decoding hundreds of frames from every source clip.
    if style == "fast_punchy" and scan_depth == "deep":
        shifts = [-0.24, -0.12, 0.0, 0.12, 0.24]
        length_offsets = [-0.06, 0.0, 0.06]
    else:
        shifts = [-0.18, 0.0, 0.18] if style == "fast_punchy" else [-0.2, 0.0, 0.2]
        length_offsets = [0.0] if style == "fast_punchy" else [-0.1, 0.0, 0.1]
    best: tuple[float, float, float, float, float] | None = None

    for shift in shifts:
        for length_offset in length_offsets:
            candidate_start = start + shift
            candidate_length = max(0.45 if style == "fast_punchy" else 0.8, base_length + length_offset)
            candidate_start = max(0.0, min(candidate_start, max(clip.duration - candidate_length - 0.03, 0.0)))
            candidate_end = min(clip.duration - 0.03, candidate_start + candidate_length)
            if candidate_end - candidate_start < 0.4:
                continue
            motion_series = _motion_series(clip, candidate_start, candidate_end, samples=4 if style == "fast_punchy" else 6)
            if len(motion_series) < 3:
                continue
            avg_motion = clamp01(float(np.mean(motion_series)) * 4.6)
            start_score = _boundary_score(motion_series[0], float(np.mean(motion_series[:2])), avg_motion)
            end_score = _boundary_score(motion_series[-1], float(np.mean(motion_series[-2:])), avg_motion)
            boundary_confidence = clamp01((start_score + end_score) / 2)
            total = (0.58 * avg_motion) + (0.42 * boundary_confidence)
            if best is None or total > best[0]:
                best = (total, candidate_start, candidate_end, boundary_confidence, avg_motion)

    if best is None:
        avg_motion = _sample_motion(clip, start, end)
        return start, end, 0.55, avg_motion
    return best[1], best[2], best[3], best[4]


def _motion_series(clip, start: float, end: float, samples: int = 6) -> list[float]:
    safe_end = max(start + 0.05, end - 0.03)
    sample_times = np.linspace(start, safe_end, num=max(3, samples))
    frames = [clip.get_frame(float(time)) for time in sample_times]
    deltas = []
    for current, following in zip(frames, frames[1:], strict=False):
        current_small = _small_gray(current)
        following_small = _small_gray(following)
        deltas.append(float(np.mean(np.abs(current_small.astype(np.float32) - following_small.astype(np.float32))) / 255.0))
    return deltas


def _boundary_score(edge_delta: float, local_average: float, avg_motion: float) -> float:
    if avg_motion <= 0.02:
        return 0.4
    ratio = edge_delta / max(avg_motion / 4.6, 0.01)
    local_ratio = edge_delta / max(local_average, 0.01)
    centered = max(0.0, 1.0 - abs(ratio - 1.0) * 0.7)
    local_centered = max(0.0, 1.0 - abs(local_ratio - 1.0) * 0.6)
    return clamp01((0.55 * centered) + (0.45 * local_centered))


def _small_gray(frame: np.ndarray) -> np.ndarray:
    rgb = frame[..., :3].astype(np.float32)
    gray = (0.299 * rgb[..., 0]) + (0.587 * rgb[..., 1]) + (0.114 * rgb[..., 2])
    step_y = max(1, gray.shape[0] // 48)
    step_x = max(1, gray.shape[1] // 27)
    return gray[::step_y, ::step_x][:48, :27]


def _recommended_speed(motion: float, window_duration: float, style: str) -> float:
    if style == "fast_punchy":
        if window_duration <= 0.65:
            return 1.0
        if motion < 0.12:
            return 1.90
        if motion < 0.22:
            return 1.65
        if motion < 0.35:
            return 1.45
        if motion < 0.55:
            return 1.20
        return 1.0
    if window_duration <= 1.2:
        return 1.0
    if motion < 0.15:
        return 1.65
    if motion < 0.28:
        return 1.35
    if motion > 0.65:
        return 1.0
    return 1.15


def _vertical_fit(width: int, height: int) -> float:
    target = 9 / 16
    ratio = width / height
    return clamp01(1.0 - abs(ratio - target) / target)


def _sharpness_quality(sharpness: float) -> float:
    if sharpness <= 0:
        return 0.0
    return clamp01((math.log1p(sharpness) - 2.0) / 5.0)


def _subject_strength(face_boxes, edge_risk: float) -> float:
    face_bonus = min(len(face_boxes), 5) * 0.12
    return clamp01(0.34 + face_bonus + (0.26 * (1.0 - edge_risk)))


def _style_fit(style: str, motion_energy: float, face_count: int, is_video: bool) -> float:
    if style == "fast_punchy":
        return clamp01(0.36 + (0.34 * motion_energy) + (0.14 if is_video else 0.0) + min(face_count, 4) * 0.04)
    if style == "founder_personal_brand":
        return clamp01(0.32 + min(face_count, 5) * 0.12 + (0.18 if is_video else 0.04))
    return clamp01(0.44 + (0.10 if is_video else 0.0) + (0.12 * (1 - min(face_count, 3) / 3)))


def _visual_role(source_type: str, face_count: int, motion: float) -> str:
    if face_count >= 2:
        return "social_energy"
    if source_type == "video" and motion > 0.45:
        return "motion_spike"
    if face_count == 1:
        return "human_focus"
    return "context"


def _candidate_tags(source_type: str, face_count: int, motion: float, orientation: str) -> list[str]:
    tags = [source_type, orientation]
    if face_count:
        tags.append("people")
    else:
        tags.append("scenery")
    if motion > 0.5:
        tags.append("high_motion")
    elif motion > 0.25:
        tags.append("medium_motion")
    else:
        tags.append("low_motion")
    return tags


def _candidate_matches_rating_token(candidate: ClipCandidate, token: str | None) -> bool:
    if not token:
        return False
    basename = Path(candidate.source_file).name
    stem = Path(candidate.source_file).stem
    if token in {candidate.candidate_id, candidate.source_file, basename, stem}:
        return True
    if "@" not in token:
        return False
    file_token, _, range_token = token.partition("@")
    if file_token not in {candidate.source_file, basename, stem}:
        return False
    try:
        start_raw, _, end_raw = range_token.partition("-")
        start = float(start_raw)
        end = float(end_raw) if end_raw else start
    except ValueError:
        return False
    midpoint = candidate.source_start + ((candidate.source_end - candidate.source_start) / 2)
    return start <= midpoint <= end


def apply_candidate_feedback(candidates: list[ClipCandidate], overrides: dict) -> list[ClipCandidate]:
    prefer_files = overrides.get("prefer_files", set())
    avoid_files = overrides.get("avoid_files", set())
    clip_ratings = overrides.get("clip_ratings", {})
    for candidate in candidates:
        delta = 0.0
        notes = []
        if any(asset_matches_override(candidate.source_file, Path(candidate.source_file).name, token) for token in prefer_files):
            delta += 0.12
            notes.append("manual prefer_files boost")
        if any(asset_matches_override(candidate.source_file, Path(candidate.source_file).name, token) for token in avoid_files):
            delta -= 0.18
            notes.append("manual avoid_files penalty")
        for token, rating in clip_ratings.items():
            if _candidate_matches_rating_token(candidate, token):
                delta += max(-3.0, min(3.0, rating)) * 0.08
                notes.append(f"manual clip rating {rating:+.1f}")
        if delta:
            candidate.score_total = clamp01(candidate.score_total + delta)
            candidate.score_breakdown["manual_feedback"] = round(delta, 4)
            candidate.why_candidate = f"{candidate.why_candidate}; {'; '.join(notes)}"
    return sorted(candidates, key=lambda candidate: (-candidate.score_total, candidate.source_file, candidate.candidate_id))
