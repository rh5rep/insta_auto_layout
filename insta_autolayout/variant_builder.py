from __future__ import annotations

from dataclasses import replace

from .layout_engine import LayoutEngine
from .models import Plan
from .ranker import choose_mode_and_target, score_assets


CAROUSEL_VARIANT_TARGETS = ("4:5", "3:4", "1:1")


class VariantPlanner:
    def build_variants(
        self,
        assets,
        requested_mode: str,
        requested_target: str | None,
        max_slides: int,
        overrides: dict,
        scan_exclusions: list[dict[str, str]],
        ranking_exclusions: list[dict[str, str]],
        variation_count: int,
    ) -> list[Plan]:
        chosen_mode, default_target = choose_mode_and_target(
            assets=assets,
            requested_mode=requested_mode,
            requested_target=requested_target,
            prefer_carousel=True,
        )
        if chosen_mode == "reel_vertical":
            score_assets(assets, default_target)
            plan = LayoutEngine().build_plan(
                assets=assets,
                chosen_mode=chosen_mode,
                target_aspect=default_target,
                max_slides=max_slides,
                overrides=overrides,
                scan_exclusions=scan_exclusions,
                ranking_exclusions=ranking_exclusions,
            )
            return [replace(plan, variant_key="reel_vertical", variant_label="Reel 9:16", variant_rank=1, variant_score=1.0)]

        specs = self._candidate_targets(default_target, requested_target, variation_count)
        plans: list[Plan] = []
        for target in specs:
            mode, _ = choose_mode_and_target(
                assets=assets,
                requested_mode="carousel",
                requested_target=target,
                prefer_carousel=True,
            )
            score_assets(assets, target)
            plan = LayoutEngine().build_plan(
                assets=assets,
                chosen_mode=mode,
                target_aspect=target,
                max_slides=max_slides,
                overrides=overrides,
                scan_exclusions=scan_exclusions,
                ranking_exclusions=ranking_exclusions,
            )
            plans.append(
                replace(
                    plan,
                    variant_key=_variant_key(target),
                    variant_label=_variant_label(target),
                    variant_score=round(self._plan_score(plan, assets), 3),
                )
            )

        primary = self._select_primary(plans, requested_target)
        alternates = [plan for plan in plans if plan.variant_key != primary.variant_key]
        alternates.sort(key=lambda plan: (-(plan.variant_score or 0.0), plan.variant_key or ""))
        ordered = [primary] + alternates
        return [replace(plan, variant_rank=index) for index, plan in enumerate(ordered, start=1)]

    def _candidate_targets(self, default_target: str, requested_target: str | None, variation_count: int) -> list[str]:
        targets = [requested_target or default_target]
        for candidate in CAROUSEL_VARIANT_TARGETS:
            if candidate not in targets:
                targets.append(candidate)
        return targets[: max(1, min(variation_count, len(targets)))]

    def _select_primary(self, plans: list[Plan], requested_target: str | None) -> Plan:
        if requested_target:
            for plan in plans:
                if plan.target_aspect_ratio == requested_target:
                    return plan
        return max(plans, key=lambda plan: ((plan.variant_score or 0.0), -len(plan.review_slides), plan.target_aspect_ratio))

    def _plan_score(self, plan: Plan, assets) -> float:
        assets_by_path = {asset.source_path: asset for asset in assets}
        slide_scores = []
        singles = 0
        collages = 0
        videos = 0
        for slide in plan.slides:
            values = [assets_by_path[path].slide_score for path in slide.source_files if path in assets_by_path]
            if values:
                slide_scores.append(sum(values) / len(values))
            if slide.kind in {"image", "hero_image"}:
                singles += 1
            elif slide.kind == "collage":
                collages += 1
            elif slide.kind == "video":
                videos += 1
        if not slide_scores:
            return 0.0

        avg_score = sum(slide_scores) / len(slide_scores)
        review_penalty = 0.05 * len(plan.review_slides)
        collage_penalty = max(0, collages - 2) * 0.03
        image_bonus = min(singles, 7) * 0.015
        video_penalty = max(0, videos - 2) * 0.04
        return avg_score + image_bonus - review_penalty - collage_penalty - video_penalty


def _variant_key(target: str) -> str:
    return f"carousel_{target.replace(':', 'x')}"


def _variant_label(target: str) -> str:
    return {
        "4:5": "Portrait 4:5",
        "3:4": "Portrait 3:4",
        "1:1": "Square 1:1",
        "9:16": "Vertical 9:16",
    }.get(target, target)
