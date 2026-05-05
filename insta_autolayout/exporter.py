from __future__ import annotations

import json
from pathlib import Path

from PIL import Image

from .collage import render_collage
from .models import MediaAsset, Plan
from .moviepy_compat import VideoFileClip
from .rendering import render_image_to_canvas
from .video_builder import ReelBuilder


class Exporter:
    def export(self, plan: Plan, assets: list[MediaAsset], output_dir: Path, dry_run: bool = False) -> None:
        output_dir.mkdir(parents=True, exist_ok=True)
        exports_dir = output_dir / "exports"
        exports_dir.mkdir(parents=True, exist_ok=True)
        assets_by_path = {asset.source_path: asset for asset in assets}

        if not dry_run:
            if plan.chosen_mode == "reel_vertical":
                ReelBuilder().build(plan, assets_by_path, output_dir / "exports" / "reel.mp4")
            else:
                for slide in plan.slides:
                    target_path = output_dir / slide.export_path
                    if slide.kind == "collage":
                        collage_assets = [assets_by_path[path] for path in slide.source_files]
                        image, _, _ = render_collage(collage_assets, self._load_image, plan.output_canvas)
                        image.save(target_path, quality=95)
                    elif slide.kind in {"image", "hero_image"}:
                        asset = assets_by_path[slide.source_files[0]]
                        with Image.open(asset.source_path) as image:
                            rendered = render_image_to_canvas(image, asset, plan.output_canvas, slide.crop_strategy)
                        rendered.save(target_path, quality=95)
                    elif slide.kind == "video":
                        asset = assets_by_path[slide.source_files[0]]
                        self._export_video(asset, target_path, plan.output_canvas, slide.crop_strategy)

        (output_dir / "plan.json").write_text(json.dumps(plan.to_dict(), indent=2), encoding="utf-8")
        (output_dir / "run_summary.txt").write_text(self.summary_text(plan), encoding="utf-8")

    def export_variants(self, plans: list[Plan], assets: list[MediaAsset], output_dir: Path, dry_run: bool = False) -> None:
        output_dir.mkdir(parents=True, exist_ok=True)
        manifest = []
        primary = plans[0]
        self.export(primary, assets, output_dir, dry_run=dry_run)
        for plan in plans:
            variant_dir = output_dir if plan.variant_rank == 1 else output_dir / "variants" / (plan.variant_key or f"variant_{plan.variant_rank}")
            if plan.variant_rank != 1:
                self.export(plan, assets, variant_dir, dry_run=dry_run)
            manifest.append(
                {
                    "variant_key": plan.variant_key,
                    "variant_label": plan.variant_label,
                    "variant_rank": plan.variant_rank,
                    "variant_score": plan.variant_score,
                    "target_aspect_ratio": plan.target_aspect_ratio,
                    "chosen_mode": plan.chosen_mode,
                    "preview_path": "preview.html" if plan.variant_rank == 1 else f"variants/{plan.variant_key}/preview.html",
                    "contact_sheet_path": "contact_sheet.jpg" if plan.variant_rank == 1 else f"variants/{plan.variant_key}/contact_sheet.jpg",
                    "plan_path": "plan.json" if plan.variant_rank == 1 else f"variants/{plan.variant_key}/plan.json",
                }
            )
        (output_dir / "variants.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    def summary_text(self, plan: Plan) -> str:
        excluded_lines = "\n".join(f"- {item['file']}: {item['reason']}" for item in plan.excluded_assets) or "- none"
        review = ", ".join(str(index) for index in plan.review_slides) or "none"
        return (
            f"variant: {plan.variant_label or 'Primary'}\n"
            f"chosen format: {plan.chosen_mode}\n"
            f"target aspect ratio: {plan.target_aspect_ratio}\n"
            f"selected assets: {len(plan.selected_assets)}\n"
            f"excluded assets:\n{excluded_lines}\n"
            f"slides needing manual review: {review}\n"
        )

    def _export_video(self, asset: MediaAsset, target_path: Path, canvas_size: tuple[int, int], strategy: str) -> None:
        if VideoFileClip is None:
            raise RuntimeError("moviepy is required to export video slides")

        canvas_width, canvas_height = canvas_size
        with VideoFileClip(asset.source_path) as clip:
            ratio = clip.w / clip.h
            target_ratio = canvas_width / canvas_height
            if strategy == "pad" or asset.edge_risk > 0.55:
                fitted = clip.resized(height=canvas_height) if ratio > target_ratio else clip.resized(width=canvas_width)
                final = fitted.with_background_color(size=canvas_size, color=(0, 0, 0), pos=("center", "center"))
            else:
                resized = clip.resized(height=canvas_height) if ratio > target_ratio else clip.resized(width=canvas_width)
                final = resized.cropped(
                    x_center=resized.w / 2,
                    y_center=resized.h / 2,
                    width=canvas_width,
                    height=canvas_height,
                )
            final.write_videofile(str(target_path), fps=min(int(clip.fps or 24), 30), codec="libx264", audio_codec="aac")

    def _load_image(self, asset: MediaAsset) -> Image.Image:
        with Image.open(asset.source_path) as image:
            return image.convert("RGB")
