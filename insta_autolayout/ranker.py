from __future__ import annotations

from collections import defaultdict
from typing import Iterable

from .models import MediaAsset
from .overrides import asset_matches_override
from .rendering import color_distance, hamming_distance


def choose_mode_and_target(
    assets: list[MediaAsset],
    requested_mode: str,
    requested_target: str | None,
    prefer_carousel: bool = True,
) -> tuple[str, str]:
    if requested_mode == "carousel":
        return _carousel_mode_for_target(requested_target or _choose_carousel_target(assets))
    if requested_mode == "reel":
        return "reel_vertical", requested_target or "9:16"

    video_count = sum(asset.media_type == "video" for asset in assets)
    short_video_count = sum(asset.media_type == "video" and (asset.duration or 0) <= 12 for asset in assets)
    portrait_count = sum(asset.orientation == "portrait" for asset in assets)
    story_score = short_video_count * 1.7 + portrait_count * 0.35

    if not prefer_carousel and video_count >= 2 and story_score >= len(assets) * 0.75:
        return "reel_vertical", requested_target or "9:16"

    return _carousel_mode_for_target(requested_target or _choose_carousel_target(assets))


def apply_asset_filters(assets: list[MediaAsset], overrides: dict) -> tuple[list[MediaAsset], list[dict[str, str]]]:
    filtered: list[MediaAsset] = []
    excluded: list[dict[str, str]] = []
    exclude_tokens = overrides.get("exclude_files", set())

    for asset in assets:
        if any(asset_matches_override(asset.source_path, asset.file_name, token) for token in exclude_tokens):
            asset.excluded_reason = "manual_exclude"
            excluded.append({"file": asset.source_path, "reason": asset.excluded_reason})
            continue
        filtered.append(asset)
    return filtered, excluded


def mark_duplicates(assets: list[MediaAsset]) -> None:
    groups: list[list[MediaAsset]] = []

    for asset in assets:
        placed = False
        for group in groups:
            if _is_near_duplicate(asset, group[0]):
                group.append(asset)
                placed = True
                break
        if not placed:
            groups.append([asset])

    for group in groups:
        if len(group) == 1:
            group[0].duplicate_group = group[0].file_name
            group[0].duplicate_score = 0.0
            continue

        representative = max(group, key=lambda item: (_sharpness_score(item), -item.edge_risk, item.file_name))
        group_id = representative.file_name
        for asset in group:
            asset.duplicate_group = group_id
            asset.duplicate_score = 0.85 if asset is not representative else 0.0
            asset.duplicate_representative = asset is representative
            if asset is not representative:
                asset.analysis_notes.append(f"near-duplicate of {representative.file_name}")


def score_assets(assets: list[MediaAsset], target_aspect: str) -> None:
    target_ratio = _target_ratio(target_aspect)
    max_sharpness = max((asset.sharpness for asset in assets), default=1.0)

    for asset in assets:
        orientation_match = _orientation_match(asset, target_ratio)
        sharpness_score = _sharpness_quality(asset.sharpness, max_sharpness)
        edge_safety = max(0.0, 1.0 - min(asset.edge_risk, 1.0))
        uniqueness = max(0.0, 1.0 - min(asset.duplicate_score, 1.0))
        people_score = min(len(asset.face_boxes), 4) / 4
        blur_penalty = _blur_penalty(asset.sharpness)
        video_penalty = 0.1 if asset.media_type == "video" and target_aspect != "9:16" else 0.0

        # Hero selection is deliberately biased toward clear, unique, crop-safe
        # images because they perform better as slide 1 in a carousel.
        asset.hero_score = (
            0.28 * orientation_match
            + 0.28 * sharpness_score
            + 0.18 * edge_safety
            + 0.12 * uniqueness
            + 0.14 * people_score
            - video_penalty
            - blur_penalty
        )
        asset.slide_score = (
            0.26 * orientation_match
            + 0.26 * sharpness_score
            + 0.18 * edge_safety
            + 0.14 * uniqueness
            + 0.08 * people_score
            + 0.08 * _duration_score(asset)
            - blur_penalty
        )
        asset.analysis_notes.append(
            f"scores hero={asset.hero_score:.2f}, slide={asset.slide_score:.2f}, target={target_aspect}, faces={len(asset.face_boxes)}, blur_penalty={blur_penalty:.2f}"
        )


def representative_assets(assets: Iterable[MediaAsset]) -> list[MediaAsset]:
    grouped: dict[str, list[MediaAsset]] = defaultdict(list)
    for asset in assets:
        grouped[asset.duplicate_group or asset.file_name].append(asset)
    reps = [max(group, key=lambda item: (item.duplicate_representative, item.slide_score, item.file_name)) for group in grouped.values()]
    return sorted(reps, key=lambda item: (-item.slide_score, item.file_name))


def pinned_asset(assets: list[MediaAsset], overrides: dict) -> MediaAsset | None:
    token = overrides.get("pin_hero")
    for asset in assets:
        if asset_matches_override(asset.source_path, asset.file_name, token):
            return asset
    return None


def force_order_assets(assets: list[MediaAsset], overrides: dict) -> list[MediaAsset]:
    ordered: list[MediaAsset] = []
    tokens = overrides.get("force_order", [])
    used = set()
    for token in tokens:
        for asset in assets:
            if asset.source_path in used:
                continue
            if asset_matches_override(asset.source_path, asset.file_name, token):
                ordered.append(asset)
                used.add(asset.source_path)
                break
    return ordered


def _choose_carousel_target(assets: list[MediaAsset]) -> str:
    portrait = sum(asset.orientation == "portrait" for asset in assets)
    square = sum(asset.orientation == "square" for asset in assets)
    if portrait >= max(2, len(assets) * 0.5):
        return "4:5"
    if square >= max(2, len(assets) * 0.45):
        return "1:1"
    return "3:4"


def _carousel_mode_for_target(target: str) -> tuple[str, str]:
    if target == "1:1":
        return "feed_carousel_square", target
    return "feed_carousel_portrait", target


def _orientation_match(asset: MediaAsset, target_ratio: float) -> float:
    ratio_gap = abs(asset.aspect_ratio - target_ratio)
    return max(0.0, 1.0 - ratio_gap / max(target_ratio, 0.01))


def _duration_score(asset: MediaAsset) -> float:
    if asset.media_type != "video":
        return 0.9
    if asset.duration is None:
        return 0.5
    if asset.duration <= 12:
        return 1.0
    if asset.duration <= 20:
        return 0.75
    return 0.45


def _is_near_duplicate(asset: MediaAsset, other: MediaAsset) -> bool:
    if asset.media_type != other.media_type:
        return False
    if asset.orientation != other.orientation:
        return False
    if abs(asset.aspect_ratio - other.aspect_ratio) > 0.12:
        return False
    if color_distance(asset.mean_color, other.mean_color) > 45:
        return False
    return (
        hamming_distance(asset.average_hash, other.average_hash) <= 4
        and hamming_distance(asset.difference_hash, other.difference_hash) <= 6
    )


def _sharpness_score(asset: MediaAsset) -> float:
    return asset.sharpness - (asset.edge_risk * 25)


def _sharpness_quality(sharpness: float, max_sharpness: float) -> float:
    relative = min(sharpness / max(max_sharpness, 1.0), 1.0)
    absolute = min(sharpness / 500.0, 1.0)
    return (0.5 * relative) + (0.5 * absolute)


def _blur_penalty(sharpness: float) -> float:
    if sharpness < 35:
        return 0.24
    if sharpness < 80:
        return 0.16
    if sharpness < 150:
        return 0.08
    return 0.0


def _target_ratio(target_aspect: str) -> float:
    left, right = target_aspect.split(":")
    return int(left) / int(right)
