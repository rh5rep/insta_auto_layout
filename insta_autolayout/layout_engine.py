from __future__ import annotations

from pathlib import Path

from .collage import choose_best_collage_layout
from .models import Plan, SlidePlan, canvas_size_for
from .overrides import asset_matches_override
from .ranker import force_order_assets, pinned_asset, representative_assets


class LayoutEngine:
    def build_plan(
        self,
        assets,
        chosen_mode: str,
        target_aspect: str,
        max_slides: int,
        overrides: dict,
        scan_exclusions: list[dict[str, str]],
        ranking_exclusions: list[dict[str, str]],
    ) -> Plan:
        canvas_size = canvas_size_for(target_aspect)
        if chosen_mode == "reel_vertical":
            return self._build_reel_plan(assets, target_aspect, canvas_size, overrides, scan_exclusions, ranking_exclusions)
        return self._build_carousel_plan(
            assets=assets,
            chosen_mode=chosen_mode,
            target_aspect=target_aspect,
            canvas_size=canvas_size,
            max_slides=max_slides,
            overrides=overrides,
            scan_exclusions=scan_exclusions,
            ranking_exclusions=ranking_exclusions,
        )

    def _build_carousel_plan(
        self,
        assets,
        chosen_mode: str,
        target_aspect: str,
        canvas_size: tuple[int, int],
        max_slides: int,
        overrides: dict,
        scan_exclusions: list[dict[str, str]],
        ranking_exclusions: list[dict[str, str]],
    ) -> Plan:
        selected_assets = self._dedupe_companion_assets(representative_assets(assets), prefer_video=False)
        hero = pinned_asset(selected_assets, overrides) or max(
            selected_assets,
            key=lambda asset: (asset.hero_score, asset.media_type == "image", asset.file_name),
        )

        force_order = [asset for asset in force_order_assets(selected_assets, overrides) if asset.source_path != hero.source_path]
        force_collages = self._build_forced_collages(selected_assets, overrides, canvas_size)
        used_in_forced = {path for group in force_collages for path in group["paths"]}

        remaining = [
            asset
            for asset in selected_assets
            if asset.source_path != hero.source_path and asset.source_path not in used_in_forced
        ]
        force_order_paths = {asset.source_path for asset in force_order}
        remaining = sorted(
            remaining,
            key=lambda asset: (
                asset.source_path not in force_order_paths,
                -asset.slide_score,
                asset.file_name,
            ),
        )

        slides: list[SlidePlan] = [
            SlidePlan(
                index=1,
                kind="hero_image" if hero.media_type == "image" else "video",
                source_files=[hero.source_path],
                crop_strategy=_default_crop_strategy(hero, target_aspect),
                why_chosen=_hero_reason(hero, overrides),
                export_path="exports/slide_01.jpg" if hero.media_type == "image" else "exports/slide_01.mp4",
                needs_manual_review=hero.edge_risk > 0.72,
                review_reason="hero crop may need checking" if hero.edge_risk > 0.72 else None,
            )
        ]

        for forced in force_collages:
            slides.append(
                SlidePlan(
                    index=len(slides) + 1,
                    kind="collage",
                    source_files=forced["paths"],
                    crop_strategy="collage_balance",
                    why_chosen=forced["why"],
                    export_path=f"exports/slide_{len(slides)+1:02d}.jpg",
                    layout_template=forced["template"],
                    needs_manual_review=forced["score"] < 0.45,
                    review_reason="forced collage fit is weak" if forced["score"] < 0.45 else None,
                )
            )

        remaining_slots = max(max_slides - len(slides), 0)
        compressed_blocks = self._build_carousel_blocks(remaining, remaining_slots)

        for block in compressed_blocks:
            index = len(slides) + 1
            if len(block) == 1:
                asset = block[0]
                slides.append(
                    SlidePlan(
                        index=index,
                        kind="video" if asset.media_type == "video" else "image",
                        source_files=[asset.source_path],
                        crop_strategy=_default_crop_strategy(asset, target_aspect),
                        why_chosen=_single_reason(asset, target_aspect),
                        export_path=f"exports/slide_{index:02d}.{'mp4' if asset.media_type == 'video' else 'jpg'}",
                        needs_manual_review=asset.edge_risk > 0.8,
                        review_reason="protected subject is close to crop edge" if asset.edge_risk > 0.8 else None,
                    )
                )
            else:
                template, ordered_assets, score = choose_best_collage_layout(block, canvas_size)
                slides.append(
                    SlidePlan(
                        index=index,
                        kind="collage",
                        source_files=[asset.source_path for asset in ordered_assets],
                        crop_strategy="collage_balance",
                        why_chosen=_collage_reason(ordered_assets),
                        export_path=f"exports/slide_{index:02d}.jpg",
                        layout_template=template.name,
                        needs_manual_review=score < 0.5,
                        review_reason="collage uses difficult aspect mix" if score < 0.5 else None,
                        extra={"layout_score": round(score, 3)},
                    )
                )

        slides = slides[:max_slides]
        review_slides = [slide.index for slide in slides if slide.needs_manual_review]
        selected = [path for slide in slides for path in slide.source_files]
        excluded_assets = scan_exclusions + ranking_exclusions + self._excluded_unselected(assets, selected)
        plan = Plan(
            chosen_mode=chosen_mode,
            target_aspect_ratio=target_aspect,
            recommended_caption_stub=_caption_stub(Path(selected[0]).parent.name if selected else "album", len(slides)),
            slides=slides,
            selected_assets=selected,
            excluded_assets=excluded_assets,
            review_slides=review_slides,
            output_canvas=canvas_size,
        )
        return plan

    def _build_reel_plan(
        self,
        assets,
        target_aspect: str,
        canvas_size: tuple[int, int],
        overrides: dict,
        scan_exclusions: list[dict[str, str]],
        ranking_exclusions: list[dict[str, str]],
    ) -> Plan:
        ordered = self._dedupe_companion_assets(representative_assets(assets), prefer_video=True)[:10]
        slides: list[SlidePlan] = []
        for index, asset in enumerate(ordered, start=1):
            duration = 2.2 if asset.media_type == "image" else min(asset.duration or 3.0, 4.0)
            slides.append(
                SlidePlan(
                    index=index,
                    kind="image_segment" if asset.media_type == "image" else "video_segment",
                    source_files=[asset.source_path],
                    crop_strategy=_default_crop_strategy(asset, target_aspect),
                    why_chosen=_single_reason(asset, target_aspect),
                    export_path="exports/reel.mp4",
                    duration=duration,
                )
            )
        return Plan(
            chosen_mode="reel_vertical",
            target_aspect_ratio=target_aspect,
            recommended_caption_stub=_caption_stub("album", len(slides)),
            slides=slides,
            selected_assets=[path for slide in slides for path in slide.source_files],
            excluded_assets=scan_exclusions + ranking_exclusions + self._excluded_unselected(
                assets,
                [path for slide in slides for path in slide.source_files],
            ),
            review_slides=[],
            output_canvas=canvas_size,
            primary_export="exports/reel.mp4",
        )

    def _build_carousel_blocks(self, assets, slot_count: int) -> list[list]:
        if not assets or slot_count <= 0:
            return []

        ordered = sorted(assets, key=lambda item: (-item.slide_score, -item.hero_score, item.file_name))
        videos = [asset for asset in ordered if asset.media_type == "video"]
        images = [asset for asset in ordered if asset.media_type == "image"]

        selected_videos = self._select_video_singles(videos, slot_count)
        remaining_slots = max(slot_count - len(selected_videos), 0)
        collage_slots = self._choose_collage_slot_count(images, remaining_slots)
        single_slots = max(0, remaining_slots - collage_slots)

        single_images = images[:single_slots]
        collage_pool = self._select_collage_pool(images[single_slots:], collage_slots)
        collage_groups = self._build_collage_groups(collage_pool, collage_slots)

        blocks = [[asset] for asset in selected_videos]
        blocks.extend([[asset] for asset in single_images])
        blocks.extend(collage_groups)
        blocks.sort(key=lambda group: (-max(item.slide_score for item in group), len(group), group[0].file_name))
        return blocks[:slot_count]

    def _select_video_singles(self, videos, slot_count: int) -> list:
        if not videos or slot_count <= 0:
            return []
        limit = min(len(videos), max(1, min(2, slot_count // 4 or 1)))
        return [asset for asset in videos[:limit] if (asset.duration or 0) <= 18]

    def _dedupe_companion_assets(self, assets, prefer_video: bool) -> list:
        grouped: dict[str, list] = {}
        ordered_keys: list[str] = []
        for asset in assets:
            key = Path(asset.file_name).stem.lower()
            if key not in grouped:
                grouped[key] = []
                ordered_keys.append(key)
            grouped[key].append(asset)

        resolved = []
        for key in ordered_keys:
            group = grouped[key]
            if len(group) == 1:
                resolved.append(group[0])
                continue

            images = [asset for asset in group if asset.media_type == "image"]
            videos = [asset for asset in group if asset.media_type == "video"]
            if images and videos:
                if prefer_video:
                    preferred = max(videos + images, key=lambda asset: (asset.media_type == "video", asset.slide_score, asset.file_name))
                else:
                    preferred = max(images + videos, key=lambda asset: (asset.media_type == "image", asset.slide_score, asset.file_name))
                resolved.append(preferred)
            else:
                resolved.append(max(group, key=lambda asset: (asset.slide_score, asset.file_name)))

        return sorted(resolved, key=lambda item: (-item.slide_score, -item.hero_score, item.file_name))

    def _choose_collage_slot_count(self, images, slot_count: int) -> int:
        if len(images) <= slot_count or slot_count <= 1:
            return 0
        return min(max(1, round(slot_count * 0.25)), max(1, slot_count // 3))

    def _select_collage_pool(self, images, collage_slots: int) -> list:
        if collage_slots <= 0 or not images:
            return []
        filtered = [asset for asset in images if asset.slide_score >= 0.42 and asset.sharpness >= 45]
        if len(filtered) < 2:
            filtered = images[: min(len(images), collage_slots * 2)]
        return filtered[: collage_slots * 3]

    def _build_collage_groups(self, images, collage_slots: int) -> list[list]:
        if collage_slots <= 0 or len(images) < 2:
            return []
        sizes = self._preferred_collage_sizes(len(images), collage_slots)
        if not sizes:
            return []

        pool = list(images)
        groups: list[list] = []
        for size in sizes:
            if len(pool) < size:
                break
            seed = pool.pop(0)
            group = [seed]
            while len(group) < size and pool:
                candidate = max(pool, key=lambda item: self._collage_compatibility(group, item))
                pool.remove(candidate)
                group.append(candidate)
            groups.append(group)
        return groups

    def _preferred_collage_sizes(self, item_count: int, collage_slots: int) -> list[int]:
        if item_count < 2 or collage_slots <= 0:
            return []
        collage_slots = min(collage_slots, max(1, item_count // 2))
        minimum_slots = max(1, (item_count + 2) // 3)
        collage_slots = max(minimum_slots, collage_slots)

        sizes = [2] * collage_slots
        extra = item_count - (2 * collage_slots)
        cursor = collage_slots - 1
        while extra > 0:
            if sizes[cursor] < 3:
                sizes[cursor] += 1
                extra -= 1
            cursor -= 1
            if cursor < 0:
                cursor = collage_slots - 1
        return sizes

    def _collage_compatibility(self, group, candidate) -> float:
        orientation_match = sum(0.35 for asset in group if asset.orientation == candidate.orientation) / len(group)
        aspect_score = sum(max(0.0, 1.0 - abs(asset.aspect_ratio - candidate.aspect_ratio)) for asset in group) / len(group)
        face_score = sum(max(0.0, 1.0 - abs(len(asset.face_boxes) - len(candidate.face_boxes)) / 4) for asset in group) / len(group)
        score_gap = sum(max(0.0, 1.0 - abs(asset.slide_score - candidate.slide_score) / 0.35) for asset in group) / len(group)
        return orientation_match + (0.25 * aspect_score) + (0.20 * face_score) + (0.20 * score_gap)

    def _build_forced_collages(self, assets, overrides: dict, canvas_size: tuple[int, int]) -> list[dict]:
        collages = []
        for group in overrides.get("force_collage", []):
            members = [
                asset
                for asset in assets
                if any(asset_matches_override(asset.source_path, asset.file_name, token) for token in group)
            ]
            members = [asset for asset in members if asset.media_type == "image"]
            if len(members) < 2:
                continue
            template, ordered_assets, score = choose_best_collage_layout(members[:4], canvas_size)
            collages.append(
                {
                    "paths": [asset.source_path for asset in ordered_assets],
                    "template": template.name,
                    "score": score,
                    "why": "manual override forced these assets into one collage slide",
                }
            )
        return collages

    def _excluded_unselected(self, assets, selected_paths: list[str]) -> list[dict[str, str]]:
        selected_set = set(selected_paths)
        excluded: list[dict[str, str]] = []
        for asset in assets:
            if asset.source_path in selected_set:
                continue
            if not asset.duplicate_representative:
                excluded.append({"file": asset.source_path, "reason": f"near_duplicate_of:{asset.duplicate_group}"})
            else:
                excluded.append({"file": asset.source_path, "reason": "lower_ranked_overflow"})
        return excluded


def _default_crop_strategy(asset, target_aspect: str) -> str:
    if asset.media_type == "video":
        return "smart_crop_or_pad"
    if asset.orientation == "landscape" and target_aspect in {"4:5", "3:4", "9:16"} and asset.edge_risk > 0.45:
        return "pad"
    return "smart_crop"


def _hero_reason(asset, overrides: dict) -> str:
    if pinned_asset([asset], overrides):
        return "manually pinned as hero slide"
    return (
        "strongest opening frame after weighting clarity, crop safety, orientation match, and uniqueness"
    )


def _single_reason(asset, target_aspect: str) -> str:
    if asset.media_type == "video":
        return f"kept as a single video slide because it scores well for pacing and fits the {target_aspect} story arc"
    return f"kept as a single image because it is sharp, unique, and crops cleanly toward {target_aspect}"


def _collage_reason(assets) -> str:
    names = ", ".join(asset.file_name for asset in assets[:3])
    return f"grouped into a collage to preserve stronger framing without awkward single-image crops: {names}"


def _caption_stub(album_name: str, slide_count: int) -> str:
    label = album_name.replace("_", " ").replace("-", " ").strip() or "album"
    return f"{label.title()} recap. {slide_count} slides, strongest moments first."
