from __future__ import annotations

import json
import math
import shutil
from pathlib import Path
from typing import Any, Callable
from xml.sax.saxutils import escape

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from .models import MediaAsset
from .moviepy_compat import AudioFileClip, ColorClip, CompositeVideoClip, ImageClip, VideoFileClip, concatenate_videoclips
from .promo_audio import PromoAudioPlanner
from .promo_models import PROMO_CANVAS, AudioVariant, PromoOutput, TimelineItem, VideoConcept
from .rendering import render_image_to_canvas
from .soundtrack_library import audio_suffix_for, Soundtrack


class PromoExporter:
    def __init__(self, audio_planner: PromoAudioPlanner | None = None) -> None:
        self.audio_planner = audio_planner or PromoAudioPlanner()

    def export_batch(
        self,
        outputs: list[PromoOutput],
        assets: list[MediaAsset],
        output_dir: Path,
        dry_run: bool = False,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        output_dir.mkdir(parents=True, exist_ok=True)
        assets_by_path = {asset.source_path: asset for asset in assets}
        overlap_report = _batch_overlap_report(outputs)
        overlap_by_id = {record["concept_id"]: record for record in overlap_report["concepts"]}
        manifest = []
        total_outputs = max(len(outputs), 1)
        for index, output in enumerate(outputs, start=1):
            if progress_callback is not None:
                progress_callback(
                    {
                        "stage": "rendering",
                        "label": f"Rendering {output.concept.concept_id}",
                        "current": index - 1,
                        "total": total_outputs,
                    }
                )
            concept_overlap = overlap_by_id.get(output.concept.concept_id, {})
            output.report["timeline_note"] = (
                "Top-level timeline is the selected clip plan before audio/BPM retiming. "
                "Variant timeline files show the exact rendered timings."
            )
            output.report["overlap"] = concept_overlap
            concept_dir = output_dir / output.concept.concept_id
            concept_dir.mkdir(parents=True, exist_ok=True)
            (concept_dir / "timeline.json").write_text(json.dumps(output.concept.to_dict(), indent=2), encoding="utf-8")
            (concept_dir / "report.json").write_text(json.dumps(output.report, indent=2), encoding="utf-8")

            variant_records = []
            for variant in output.variants:
                variant_dir = concept_dir / variant.render_name
                variant_dir.mkdir(parents=True, exist_ok=True)
                soundtrack = None
                if variant.track_title:
                    soundtrack = Soundtrack(
                        track_id=variant.track_label or variant.track_title,
                        title=variant.track_title,
                        source=variant.track_source or "unknown",
                        license_name=variant.track_license,
                        credit=variant.track_credit,
                        bpm=variant.bpm,
                        energy=None,
                        tags=(),
                        local_path=variant.track_original_path,
                        download_url=variant.track_download_url,
                        page_url=variant.track_page_url,
                    )
                audio_suffix = ".wav" if variant.audio_mode == "generated_sound" else audio_suffix_for(soundtrack) if soundtrack else ".mp3"
                audio_path = variant_dir / f"audio{audio_suffix}"
                prepared_audio = self.audio_planner.prepare_audio_asset(variant, audio_path, dry_run=dry_run)
                if variant.has_audio and prepared_audio is None:
                    variant.has_audio = False
                    variant.report_notes.append("Audio asset could not be prepared; rendered without audio.")
                (variant_dir / "variant.json").write_text(json.dumps(variant.to_dict(), indent=2), encoding="utf-8")
                (variant_dir / "timeline.json").write_text(json.dumps(_timeline_json(output.concept, variant), indent=2), encoding="utf-8")
                (variant_dir / "sequence.fcpxml").write_text(_fcpxml_text(output.concept, variant), encoding="utf-8")
                mp4_path = variant_dir / "final.mp4"
                if not dry_run:
                    self._render_variant(
                        variant,
                        assets_by_path,
                        mp4_path,
                        prepared_audio if variant.has_audio else None,
                        output.report.get("text_overlays"),
                        output.report.get("brand_cards"),
                    )
                variant_records.append(
                    {
                        "audio_mode": variant.audio_mode,
                        "display_name": variant.display_name,
                        "path": str(mp4_path.relative_to(output_dir)),
                    }
                )

            manifest.append(
                {
                    "concept_id": output.concept.concept_id,
                    "style": output.concept.style,
                    "strategy": output.concept.strategy,
                    "target_duration": output.concept.target_duration,
                    "clip_count": len(output.concept.timeline),
                    "overlap": _manifest_overlap(concept_overlap),
                    "variants": variant_records,
                }
            )
            if progress_callback is not None:
                progress_callback(
                    {
                        "stage": "rendering",
                        "label": f"Rendered {output.concept.concept_id}",
                        "current": index,
                        "total": total_outputs,
                    }
                )

        (output_dir / "overlap_report.json").write_text(json.dumps(overlap_report, indent=2), encoding="utf-8")
        (output_dir / "batch_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        write_review_assets(output_dir, manifest)

    def _render_variant(
        self,
        variant: AudioVariant,
        assets_by_path: dict[str, MediaAsset],
        output_path: Path,
        audio_path: Path | None,
        text_overlays: dict[str, Any] | None = None,
        brand_cards: dict[str, Any] | None = None,
    ) -> None:
        clips = []
        temp_audio: list[AudioFileClip] = []
        try:
            for item in variant.timeline:
                asset = assets_by_path[item.source_file]
                if item.source_type == "image":
                    clip = self._image_clip(asset, item)
                else:
                    clip = self._video_clip(asset, item)
                clips.append(clip)

            content = concatenate_videoclips(clips, method="compose", padding=-0.08)
            if text_overlays:
                content = _apply_text_overlay(content, text_overlays, PROMO_CANVAS)
            sequence_clips = _with_brand_cards([content], brand_cards, PROMO_CANVAS)
            final = concatenate_videoclips(sequence_clips, method="compose", padding=-0.08)
            if audio_path and audio_path.exists() and AudioFileClip is not None:
                audio = AudioFileClip(str(audio_path))
                if final.duration is not None:
                    safe_duration = max(0.1, float(final.duration))
                    audio_source_duration = float(audio.duration or safe_duration)
                    start_sec = max(0.0, min(float(variant.track_start_sec), max(audio_source_duration - safe_duration, 0.0)))
                    audio_duration = min(safe_duration, max(audio_source_duration - start_sec, 0.0))
                    audio = audio.subclipped(start_sec, start_sec + audio_duration)
                    if audio_duration >= safe_duration:
                        audio = audio.with_duration(safe_duration)
                temp_audio.append(audio)
                final = final.with_audio(audio)
                final.write_videofile(str(output_path), fps=30, codec="libx264", audio_codec="aac", logger=None)
            else:
                final = final.with_audio(None)
                final.write_videofile(str(output_path), fps=30, codec="libx264", audio=False, logger=None)
            final.close()
        finally:
            for clip in clips:
                try:
                    clip.close()
                except Exception:
                    pass
            for audio in temp_audio:
                try:
                    audio.close()
                except Exception:
                    pass

    def _image_clip(self, asset: MediaAsset, item) -> ImageClip:
        with Image.open(asset.source_path) as image:
            rendered = render_image_to_canvas(image, asset, PROMO_CANVAS, item.crop_strategy if item.crop_strategy != "smart_crop_or_pad" else "smart_crop")
        clip = ImageClip(np.array(rendered)).with_duration(item.duration)
        if item.motion_treatment == "zoom_pulse":
            clip = clip.resized(lambda t: 1.0 + 0.05 * (t / max(item.duration, 0.1))).with_position("center")
        return clip

    def _video_clip(self, asset: MediaAsset, item):
        source_clip = VideoFileClip(asset.source_path, audio=False)
        safe_end = min(item.source_end, max(item.source_start + 0.06, source_clip.duration - 0.03))
        clip = source_clip.subclipped(item.source_start, safe_end).with_audio(None)
        if item.playback_rate != 1.0:
            clip = clip.with_speed_scaled(factor=item.playback_rate)
        fitted = _fit_video_to_canvas(clip, PROMO_CANVAS, item.crop_strategy)
        return fitted.with_duration(item.duration)


def _apply_text_overlay(clip, text_overlays: dict[str, Any], canvas_size: tuple[int, int]):
    overlay_image = _overlay_image(text_overlays, canvas_size)
    overlay = ImageClip(np.array(overlay_image)).with_duration(float(clip.duration or 0.0)).with_position(("center", "center"))
    return CompositeVideoClip([clip, overlay], size=canvas_size)


def _with_brand_cards(clips: list, brand_cards: dict[str, Any] | None, canvas_size: tuple[int, int]) -> list:
    if not brand_cards:
        return clips
    result = []
    if isinstance(brand_cards.get("intro"), dict):
        duration = _duration_value(brand_cards.get("intro_duration"), 1.1)
        result.append(_brand_card_clip(brand_cards, brand_cards["intro"], canvas_size, duration, "intro"))
    result.extend(clips)
    if isinstance(brand_cards.get("outro"), dict):
        duration = _duration_value(brand_cards.get("outro_duration"), 1.35)
        result.append(_brand_card_clip(brand_cards, brand_cards["outro"], canvas_size, duration, "outro"))
    return result


def _brand_card_clip(brand_cards: dict[str, Any], content: dict[str, Any], canvas_size: tuple[int, int], duration: float, role: str):
    image = _brand_card_image(brand_cards, content, canvas_size, role)
    clip = ImageClip(np.array(image)).with_duration(duration)
    if role == "intro":
        return clip.resized(lambda t: 1.0 + 0.018 * min(max(t / max(duration, 0.1), 0.0), 1.0)).with_position("center")
    return clip


def _brand_card_image(brand_cards: dict[str, Any], content: dict[str, Any], canvas_size: tuple[int, int], role: str) -> Image.Image:
    width, height = canvas_size
    image = Image.new("RGB", canvas_size, (13, 14, 16))
    draw = ImageDraw.Draw(image, "RGBA")
    _draw_brand_background(draw, width, height)

    logo = _load_logo(brand_cards.get("logo_path"))
    if logo:
        logo_width = 190 if role == "intro" else 150
        logo = _fit_logo(logo, logo_width)
        image.paste(logo, (78, 94), logo)

    eyebrow = str(content.get("eyebrow") or "TRYBE").strip().upper()
    headline = str(content.get("headline") or "").strip()
    subheadline = str(content.get("subheadline") or "").strip()
    cta = str(content.get("cta") or "").strip()
    proof = str(content.get("proof") or "").strip()

    margin_x = 78
    max_text_width = width - 156
    eyebrow_font = _font(30, bold=True)
    headline_font = _font(100 if role == "intro" else 86, bold=True)
    sub_font = _font(43)
    cta_font = _font(40, bold=True)
    proof_font = _font(31, bold=True)

    draw.rounded_rectangle((margin_x, 326, margin_x + 250, 388), radius=31, fill=(255, 106, 43, 235))
    draw.text((margin_x + 30, 345), eyebrow, font=eyebrow_font, fill=(255, 247, 241, 255))

    cursor = 482 if role == "intro" else 520
    for line in _wrap_text(headline, headline_font, max_text_width, draw):
        draw.text((margin_x, cursor), line, font=headline_font, fill=(255, 248, 242, 255))
        cursor += draw.textbbox((0, 0), line, font=headline_font)[3] + 20
    cursor += 18
    for line in _wrap_text(subheadline, sub_font, max_text_width, draw):
        draw.text((margin_x, cursor), line, font=sub_font, fill=(255, 207, 184, 255))
        cursor += draw.textbbox((0, 0), line, font=sub_font)[3] + 16

    if proof:
        proof_y = 1306
        draw.rounded_rectangle((margin_x, proof_y, width - margin_x, proof_y + 88), radius=44, fill=(23, 58, 51, 224))
        draw.text((margin_x + 36, proof_y + 27), proof, font=proof_font, fill=(229, 244, 239, 255))

    if cta:
        cta_y = 1588 if role == "intro" else 1508
        bbox = draw.textbbox((0, 0), cta, font=cta_font)
        pill_w = min(width - (margin_x * 2), bbox[2] - bbox[0] + 66)
        draw.rounded_rectangle((margin_x, cta_y, margin_x + pill_w, cta_y + 86), radius=43, fill=(255, 245, 237, 245))
        draw.text((margin_x + 33, cta_y + 24), cta, font=cta_font, fill=(16, 19, 18, 255))

    footer = str(brand_cards.get("footer") or "Try something new. Meet people for real.").strip()
    draw.text((margin_x, 1788), "TRYBE", font=_font(72, bold=True), fill=(255, 248, 242, 255))
    draw.text((margin_x, 1850), footer, font=_font(31, bold=True), fill=(255, 207, 184, 255))
    return image.convert("RGBA")


def _draw_brand_background(draw: ImageDraw.ImageDraw, width: int, height: int) -> None:
    draw.rectangle((0, 0, width, height), fill=(13, 14, 16, 255))
    for radius, alpha in ((360, 62), (520, 34), (700, 18)):
        draw.ellipse((width - radius, 80, width + radius, 80 + radius * 2), fill=(255, 106, 43, alpha))
    for radius, alpha in ((420, 72), (620, 34), (820, 16)):
        draw.ellipse((-radius, height - radius, radius, height + radius), fill=(23, 58, 51, alpha))
    draw.line((140, 850, 930, 520), fill=(255, 106, 43, 150), width=18)
    draw.line((520, 1040, 970, 700), fill=(255, 190, 142, 84), width=8)


def _load_logo(path_value: object) -> Image.Image | None:
    if not path_value:
        return None
    try:
        path = Path(str(path_value)).expanduser()
        if not path.exists():
            return None
        return Image.open(path).convert("RGBA")
    except Exception:
        return None


def _fit_logo(logo: Image.Image, target_width: int) -> Image.Image:
    ratio = target_width / max(1, logo.width)
    return logo.resize((target_width, max(1, int(logo.height * ratio))), Image.Resampling.LANCZOS)


def _duration_value(value: object, fallback: float) -> float:
    try:
        return max(0.3, min(float(value), 4.0))
    except (TypeError, ValueError):
        return fallback


def _overlay_image(text_overlays: dict[str, Any], canvas_size: tuple[int, int]) -> Image.Image:
    width, height = canvas_size
    headline = str(text_overlays.get("headline") or "").strip()
    subheadline = str(text_overlays.get("subheadline") or "").strip()
    cta = str(text_overlays.get("cta") or "").strip()
    label = str(text_overlays.get("label") or "").strip()
    placement = str(text_overlays.get("placement") or "bottom").strip().lower()
    if not any((headline, subheadline, cta, label)):
        return Image.new("RGBA", canvas_size, (0, 0, 0, 0))

    image = Image.new("RGBA", canvas_size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    margin_x = 72
    max_text_width = width - (margin_x * 2)
    headline_font = _font(78, bold=True)
    sub_font = _font(42)
    cta_font = _font(38, bold=True)
    label_font = _font(30, bold=True)

    rows: list[tuple[str, ImageFont.FreeTypeFont | ImageFont.ImageFont, tuple[int, int, int, int], int, bool]] = []
    for line in _wrap_text(headline, headline_font, max_text_width, draw):
        rows.append((line, headline_font, (255, 255, 255, 255), 16, False))
    for line in _wrap_text(subheadline, sub_font, max_text_width, draw):
        rows.append((line, sub_font, (236, 244, 241, 245), 10, False))
    if cta:
        if rows:
            text, font, color, gap, pill = rows[-1]
            rows[-1] = (text, font, color, max(gap, 34), pill)
        rows.append((cta, cta_font, (18, 24, 22, 255), 0, True))

    metrics = []
    block_height = 0
    for text, font, color, gap, pill in rows:
        bbox = draw.textbbox((0, 0), text, font=font)
        line_height = bbox[3] - bbox[1]
        if pill:
            line_height += 28
        metrics.append((text, font, color, gap, pill, line_height, bbox))
        block_height += line_height + gap
    if metrics:
        block_height -= metrics[-1][3]

    y = 130 if placement == "top" else height - block_height - 170
    panel_top = max(0, y - 44)
    panel_bottom = min(height, y + block_height + 52)
    _draw_vertical_scrim(draw, width, panel_top, panel_bottom)

    if label:
        label_line = _wrap_text(label.upper(), label_font, max_text_width, draw)[:1]
        if label_line:
            text = label_line[0]
            bbox = draw.textbbox((0, 0), text, font=label_font)
            badge_w = (bbox[2] - bbox[0]) + 36
            badge_h = (bbox[3] - bbox[1]) + 22
            badge_y = max(40, y - badge_h - 34)
            draw.rounded_rectangle((margin_x, badge_y, margin_x + badge_w, badge_y + badge_h), radius=18, fill=(255, 106, 43, 230))
            draw.text((margin_x + 18, badge_y + 10), text, font=label_font, fill=(255, 255, 255, 255))

    cursor = y
    for text, font, color, gap, pill, line_height, bbox in metrics:
        if pill:
            pill_w = (bbox[2] - bbox[0]) + 48
            draw.rounded_rectangle((margin_x, cursor, margin_x + pill_w, cursor + line_height), radius=24, fill=(245, 247, 242, 238))
            draw.text((margin_x + 24, cursor + 14), text, font=font, fill=color)
        else:
            draw.text((margin_x, cursor), text, font=font, fill=color, stroke_width=2, stroke_fill=(0, 0, 0, 150))
        cursor += line_height + gap
    return image


def _draw_vertical_scrim(draw: ImageDraw.ImageDraw, width: int, top: int, bottom: int) -> None:
    for offset, y in enumerate(range(top, bottom)):
        distance = min(offset, bottom - y)
        alpha = int(168 * min(1.0, max(0.0, distance / 70)))
        draw.line((0, y, width, y), fill=(0, 0, 0, alpha))


def _wrap_text(text: str, font: ImageFont.FreeTypeFont | ImageFont.ImageFont, max_width: int, draw: ImageDraw.ImageDraw) -> list[str]:
    if not text:
        return []
    lines: list[str] = []
    current = ""
    for word in text.split():
        candidate = f"{current} {word}".strip()
        if draw.textbbox((0, 0), candidate, font=font)[2] <= max_width:
            current = candidate
            continue
        if current:
            lines.append(current)
        current = word
    if current:
        lines.append(current)
    return lines[:3]


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf" if bold else "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/System/Library/Fonts/Supplemental/Helvetica Bold.ttf" if bold else "/System/Library/Fonts/Supplemental/Helvetica.ttf",
        "/Library/Fonts/Arial Bold.ttf" if bold else "/Library/Fonts/Arial.ttf",
    ]
    for path in candidates:
        try:
            return ImageFont.truetype(path, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def write_review_assets(output_dir: Path, manifest: list[dict[str, Any]]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest = _enrich_review_manifest(output_dir, manifest)
    (output_dir / "index.html").write_text(_index_html(manifest), encoding="utf-8")
    (output_dir / "review.html").write_text(_review_html_v2(), encoding="utf-8")


def refresh_review_assets(output_dir: Path) -> bool:
    output_dir = output_dir.expanduser().resolve()
    manifest_path = output_dir / "batch_manifest.json"
    if not manifest_path.exists():
        return False
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, list):
        return False
    write_review_assets(output_dir, manifest)
    return True


def _enrich_review_manifest(output_dir: Path, manifest: list[dict[str, Any]]) -> list[dict[str, Any]]:
    enriched = []
    for record in manifest:
        item = dict(record)
        concept_id = str(item.get("concept_id") or "")
        report_path = output_dir / concept_id / "report.json"
        if report_path.exists():
            try:
                report = json.loads(report_path.read_text(encoding="utf-8"))
            except Exception:
                report = {}
            timeline = report.get("timeline") if isinstance(report, dict) else None
            if isinstance(timeline, list):
                item["clip_count"] = len(timeline)
            if not item.get("target_duration") and isinstance(timeline, list) and timeline:
                item["target_duration"] = float(timeline[-1].get("timeline_end") or 0.0)
            if not item.get("strategy") and isinstance(report, dict):
                item["strategy"] = report.get("strategy") or item.get("strategy")
        item.setdefault("clip_count", 0)
        item.setdefault("target_duration", 0.0)
        item.setdefault("strategy", "unknown")
        enriched.append(item)
    return enriched


def _fit_video_to_canvas(clip, canvas_size: tuple[int, int], crop_strategy: str):
    width, height = canvas_size
    ratio = clip.w / clip.h
    target_ratio = width / height
    if crop_strategy in {"pad", "smart_crop_or_pad"} and abs(ratio - target_ratio) > 0.22:
        fitted = clip.resized(height=height) if ratio > target_ratio else clip.resized(width=width)
        return fitted.with_background_color(size=canvas_size, color=(0, 0, 0), pos=("center", "center"))
    resized = clip.resized(height=height) if ratio > target_ratio else clip.resized(width=width)
    return resized.cropped(x_center=resized.w / 2, y_center=resized.h / 2, width=width, height=height)


def _timeline_json(concept: VideoConcept, variant: AudioVariant) -> dict:
    return {
        "concept_id": concept.concept_id,
        "style": concept.style,
        "strategy": concept.strategy,
        "audio_mode": variant.audio_mode,
        "bpm": variant.bpm,
        "track_label": variant.track_label,
        "track_title": variant.track_title,
        "track_source": variant.track_source,
        "track_license": variant.track_license,
        "track_credit": variant.track_credit,
        "track_page_url": variant.track_page_url,
        "track_start_sec": variant.track_start_sec,
        "selection_reason": variant.selection_reason,
        "timeline": [item.to_dict() for item in variant.timeline],
    }


def _fcpxml_text(concept: VideoConcept, variant: AudioVariant) -> str:
    duration = variant.timeline[-1].timeline_end if variant.timeline else 0.0
    resources = [
        '<format id="r1" name="FFVideoFormat1080x1920p30" frameDuration="100/3000s" width="1080" height="1920" colorSpace="1-1-1 (Rec. 709)"/>'
    ]
    asset_lines = []
    seen = {}
    next_id = 2
    for item in variant.timeline:
        if item.source_file in seen:
            continue
        asset_id = f"r{next_id}"
        next_id += 1
        seen[item.source_file] = asset_id
        src = Path(item.source_file).resolve().as_uri()
        asset_lines.append(
            f'<asset id="{asset_id}" name="{escape(Path(item.source_file).name)}" src="{src}" start="0s" hasVideo="1" hasAudio="0" format="r1" duration="{_time_expr(max(item.source_end, item.duration) + 1)}"/>'
        )
    resources.extend(asset_lines)

    spine = []
    for item in variant.timeline:
        ref = seen[item.source_file]
        spine.append(
            f'<asset-clip name="{escape(Path(item.source_file).name)}" ref="{ref}" offset="{_time_expr(item.timeline_start)}" start="{_time_expr(item.source_start)}" duration="{_time_expr(item.duration)}"/>'
        )

    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE fcpxml>\n'
        '<fcpxml version="1.10">\n'
        '  <resources>\n'
        f'    {"".join(resources)}\n'
        '  </resources>\n'
        '  <library>\n'
        '    <event name="insta_autolayout">\n'
        f'      <project name="{concept.concept_id}_{variant.render_name}">\n'
        f'        <sequence format="r1" duration="{_time_expr(duration)}" tcStart="0s" tcFormat="NDF" audioLayout="stereo" audioRate="48k">\n'
        '          <spine>\n'
        f'            {"".join(spine)}\n'
        '          </spine>\n'
        '        </sequence>\n'
        '      </project>\n'
        '    </event>\n'
        '  </library>\n'
        '</fcpxml>\n'
    )


def _time_expr(seconds: float) -> str:
    value = int(round(seconds * 1000))
    return f"{value}/1000s"


def _batch_overlap_report(outputs: list[PromoOutput]) -> dict[str, Any]:
    concept_sets = [_concept_sets(output.concept) for output in outputs]
    pairwise: list[dict[str, Any]] = []
    concepts: list[dict[str, Any]] = []
    for index, output in enumerate(outputs):
        current = concept_sets[index]
        earlier_metrics = []
        for previous_index in range(index):
            previous = concept_sets[previous_index]
            metrics = _overlap_metrics(current, previous)
            pairwise.append(metrics)
            earlier_metrics.append(metrics)

        most_similar = max(earlier_metrics, key=_similarity_sort_key, default=None)
        concepts.append(
            {
                "concept_id": output.concept.concept_id,
                "strategy": output.concept.strategy,
                "clip_count": len(output.concept.timeline),
                "unique_candidate_count": len(current["candidate_ids"]),
                "unique_source_count": len(current["source_files"]),
                "unique_source_stem_count": len(current["source_stems"]),
                "max_candidate_jaccard": _max_metric(earlier_metrics, "candidate_jaccard"),
                "max_source_jaccard": _max_metric(earlier_metrics, "source_jaccard"),
                "max_source_stem_jaccard": _max_metric(earlier_metrics, "source_stem_jaccard"),
                "max_shared_candidate_count": _max_metric(earlier_metrics, "shared_candidate_count", integer=True),
                "max_shared_source_count": _max_metric(earlier_metrics, "shared_source_count", integer=True),
                "max_shared_source_stem_count": _max_metric(earlier_metrics, "shared_source_stem_count", integer=True),
                "most_similar_previous": _compact_pairwise(most_similar) if most_similar else None,
                "overlap_with_earlier": [_compact_pairwise(metric) for metric in earlier_metrics],
            }
        )

    return {
        "summary": _overlap_summary(outputs, concept_sets),
        "concepts": concepts,
        "pairwise": pairwise,
    }


def _concept_sets(concept: VideoConcept) -> dict[str, set[str]]:
    return {
        "concept_id": {concept.concept_id},
        "candidate_ids": {item.candidate_id for item in concept.timeline},
        "source_files": {item.source_file for item in concept.timeline},
        "source_stems": {_source_stem(item) for item in concept.timeline},
    }


def _source_stem(item: TimelineItem) -> str:
    return Path(item.source_file).stem.lower()


def _overlap_metrics(current: dict[str, set[str]], previous: dict[str, set[str]]) -> dict[str, Any]:
    current_id = next(iter(current["concept_id"]))
    previous_id = next(iter(previous["concept_id"]))
    shared_candidates = sorted(current["candidate_ids"] & previous["candidate_ids"])
    shared_sources = sorted(current["source_files"] & previous["source_files"], key=lambda value: Path(value).name.lower())
    shared_stems = sorted(current["source_stems"] & previous["source_stems"])
    return {
        "concept_id": current_id,
        "previous_concept_id": previous_id,
        "shared_candidate_count": len(shared_candidates),
        "candidate_jaccard": _jaccard(current["candidate_ids"], previous["candidate_ids"]),
        "shared_candidate_ids": shared_candidates,
        "shared_source_count": len(shared_sources),
        "source_jaccard": _jaccard(current["source_files"], previous["source_files"]),
        "shared_source_files": shared_sources,
        "shared_source_stem_count": len(shared_stems),
        "source_stem_jaccard": _jaccard(current["source_stems"], previous["source_stems"]),
        "shared_source_stems": shared_stems,
        "similarity_score": round(
            max(
                _jaccard(current["candidate_ids"], previous["candidate_ids"]),
                _jaccard(current["source_files"], previous["source_files"]),
                _jaccard(current["source_stems"], previous["source_stems"]),
            ),
            4,
        ),
    }


def _overlap_summary(outputs: list[PromoOutput], concept_sets: list[dict[str, set[str]]]) -> dict[str, Any]:
    source_usage: dict[str, set[str]] = {}
    stem_usage: dict[str, set[str]] = {}
    candidate_usage: dict[str, set[str]] = {}
    total_clip_placements = 0
    for output, sets in zip(outputs, concept_sets, strict=False):
        concept_id = output.concept.concept_id
        total_clip_placements += len(output.concept.timeline)
        for value in sets["source_files"]:
            source_usage.setdefault(value, set()).add(concept_id)
        for value in sets["source_stems"]:
            stem_usage.setdefault(value, set()).add(concept_id)
        for value in sets["candidate_ids"]:
            candidate_usage.setdefault(value, set()).add(concept_id)

    return {
        "concept_count": len(outputs),
        "total_clip_placements": total_clip_placements,
        "unique_candidate_count": len(candidate_usage),
        "unique_source_count": len(source_usage),
        "unique_source_stem_count": len(stem_usage),
        "reused_candidates": _usage_records(candidate_usage, "candidate_id"),
        "reused_source_files": _usage_records(source_usage, "source_file", path_sort=True),
        "reused_source_stems": _usage_records(stem_usage, "source_stem"),
    }


def _usage_records(usage: dict[str, set[str]], key: str, path_sort: bool = False) -> list[dict[str, Any]]:
    records = [
        {key: value, "use_count": len(concept_ids), "concept_ids": sorted(concept_ids)}
        for value, concept_ids in usage.items()
        if len(concept_ids) > 1
    ]
    if path_sort:
        return sorted(records, key=lambda item: (-item["use_count"], Path(str(item[key])).name.lower()))
    return sorted(records, key=lambda item: (-item["use_count"], str(item[key]).lower()))


def _jaccard(left: set[str], right: set[str]) -> float:
    if not left and not right:
        return 0.0
    return round(len(left & right) / max(len(left | right), 1), 4)


def _max_metric(metrics: list[dict[str, Any]], key: str, integer: bool = False) -> float | int:
    if not metrics:
        return 0 if integer else 0.0
    value = max(metric[key] for metric in metrics)
    return int(value) if integer else round(float(value), 4)


def _similarity_sort_key(metric: dict[str, Any]) -> tuple[float, int, int, int]:
    return (
        float(metric["similarity_score"]),
        int(metric["shared_source_stem_count"]),
        int(metric["shared_source_count"]),
        int(metric["shared_candidate_count"]),
    )


def _compact_pairwise(metric: dict[str, Any]) -> dict[str, Any]:
    return {
        "previous_concept_id": metric["previous_concept_id"],
        "similarity_score": metric["similarity_score"],
        "shared_candidate_count": metric["shared_candidate_count"],
        "candidate_jaccard": metric["candidate_jaccard"],
        "shared_candidate_ids": metric["shared_candidate_ids"],
        "shared_source_count": metric["shared_source_count"],
        "source_jaccard": metric["source_jaccard"],
        "shared_source_files": metric["shared_source_files"],
        "shared_source_stem_count": metric["shared_source_stem_count"],
        "source_stem_jaccard": metric["source_stem_jaccard"],
        "shared_source_stems": metric["shared_source_stems"],
    }


def _manifest_overlap(overlap: dict[str, Any]) -> dict[str, Any]:
    most_similar = overlap.get("most_similar_previous") or {}
    return {
        "max_candidate_jaccard": overlap.get("max_candidate_jaccard", 0.0),
        "max_source_jaccard": overlap.get("max_source_jaccard", 0.0),
        "max_source_stem_jaccard": overlap.get("max_source_stem_jaccard", 0.0),
        "max_shared_source_count": overlap.get("max_shared_source_count", 0),
        "most_similar_previous": most_similar.get("previous_concept_id"),
        "most_similar_score": most_similar.get("similarity_score", 0.0),
    }


def _index_html(manifest: list[dict]) -> str:
    concept_count = len(manifest)
    avg_duration = sum(float(record.get("target_duration", 0.0)) for record in manifest) / max(concept_count, 1)
    avg_overlap = sum(float((record.get("overlap") or {}).get("max_source_jaccard", 0.0)) for record in manifest) / max(concept_count, 1)
    highest_overlap = max((record for record in manifest), key=lambda item: float((item.get("overlap") or {}).get("max_source_jaccard", 0.0)), default=None)
    cards = []
    for record in manifest:
        primary_variant = record["variants"][0] if record.get("variants") else None
        overlap = record.get("overlap", {})
        previous = overlap.get("most_similar_previous") or "none"
        overlap_value = float(overlap.get("max_source_jaccard", 0.0))
        overlap_label = _overlap_label(overlap_value)
        overlap_class = _overlap_class(overlap_value)
        overlap_line = (
            f'<div class="metric-row"><span>Source overlap</span><strong>{overlap_value:.2f}</strong></div>'
            f'<div class="metric-row"><span>Scene overlap</span><strong>{float(overlap.get("max_source_stem_jaccard", 0.0)):.2f}</strong></div>'
            f'<div class="metric-row"><span>Closest previous</span><strong>{previous}</strong></div>'
        )
        review_link = f'review.html?concept={record["concept_id"]}'
        play_link = primary_variant["path"] if primary_variant else "#"
        play_label = primary_variant["display_name"] if primary_variant else "No variant"
        preview_block = (
            f'<video class="card-video" preload="metadata" controls muted playsinline src="{play_link}"></video>'
            if primary_variant
            else '<div class="card-video placeholder">No preview</div>'
        )
        cards.append(
            f"""
            <article class="card">
              {preview_block}
              <div class="card-top">
                <div>
                  <p class="eyebrow">{record["concept_id"]}</p>
                  <h2>{_strategy_label(record["strategy"])}</h2>
                </div>
                <span class="pill {overlap_class}">{overlap_label}</span>
              </div>
              <div class="metric-grid">
                <div class="metric">
                  <span>Target</span>
                  <strong>{record["target_duration"]:.1f}s</strong>
                </div>
                <div class="metric">
                  <span>Clips</span>
                  <strong>{int(record.get("clip_count", 0))}</strong>
                </div>
                <div class="metric">
                  <span>Timing</span>
                  <strong>{_primary_bpm(play_label)}</strong>
                </div>
              </div>
              <div class="overlap-box">
                <div class="metric-row"><span>Reuse risk</span><strong>{overlap_label}</strong></div>
                {overlap_line}
              </div>
              <div class="cta-row">
                <a class="button button-primary" href="{review_link}">Open Review</a>
                <a class="button" href="{play_link}">Play MP4</a>
              </div>
            </article>
            """
        )
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>insta_autolayout batch review</title>
  <style>
    :root {{
      --bg: #f4eee4;
      --panel: #fffdf8;
      --ink: #201815;
      --muted: #6e5a50;
      --line: #d8c6b4;
      --accent: #a34b2a;
      --accent-soft: #efe0d4;
      --ok: #2d6a4f;
      --warn: #9a6b17;
      --risk: #9d2a1f;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      color: var(--ink);
      background:
        radial-gradient(circle at top left, #fff6ea 0, transparent 34rem),
        linear-gradient(180deg, #f8f2e8 0%, var(--bg) 55%, #efe7db 100%);
      font-family: "Iowan Old Style", "Palatino Linotype", "Book Antiqua", Georgia, serif;
    }}
    main {{ max-width: 1180px; margin: 0 auto; padding: 36px 20px 72px; }}
    .hero {{
      display: grid;
      grid-template-columns: 1.4fr 1fr;
      gap: 18px;
      margin-bottom: 22px;
    }}
    .hero-card, .summary-card, .card {{
      background: color-mix(in srgb, var(--panel) 92%, white 8%);
      border: 1px solid var(--line);
      border-radius: 24px;
      padding: 22px;
      box-shadow: 0 12px 30px rgba(48, 28, 18, 0.05);
    }}
    .hero-card h1 {{ margin: 0 0 10px; font-size: clamp(2.6rem, 5vw, 4.4rem); line-height: 0.95; }}
    .hero-card p {{ margin: 0; color: var(--muted); max-width: 52ch; font-size: 1.06rem; }}
    .hero-top {{
      display: flex;
      align-items: start;
      justify-content: space-between;
      gap: 14px;
      margin-bottom: 10px;
    }}
    .help-button {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      padding: 10px 14px;
      border-radius: 999px;
      border: 1px solid var(--line);
      background: rgba(255,255,255,0.74);
      color: var(--ink);
      cursor: pointer;
      font-weight: 700;
      font: inherit;
    }}
    .flow-modal {{
      border: 0;
      padding: 0;
      max-width: min(920px, calc(100vw - 32px));
      width: 100%;
      background: transparent;
    }}
    .flow-modal::backdrop {{
      background: rgba(20, 12, 9, 0.56);
      backdrop-filter: blur(4px);
    }}
    .flow-sheet {{
      border: 1px solid var(--line);
      border-radius: 26px;
      background: color-mix(in srgb, var(--panel) 94%, white 6%);
      padding: 22px;
      box-shadow: 0 20px 60px rgba(28, 18, 14, 0.18);
    }}
    .flow-top {{
      display: flex;
      justify-content: space-between;
      gap: 16px;
      align-items: start;
      margin-bottom: 14px;
    }}
    .flow-top h2 {{ margin: 0; font-size: 2rem; line-height: 0.95; }}
    .flow-top p {{ margin: 6px 0 0; color: var(--muted); max-width: 48ch; }}
    .close-button {{
      border: 1px solid var(--line);
      background: rgba(255,255,255,0.8);
      color: var(--ink);
      border-radius: 999px;
      padding: 9px 12px;
      cursor: pointer;
      font-weight: 700;
      font: inherit;
    }}
    .flow-grid {{
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 12px;
      margin-top: 14px;
    }}
    .flow-step {{
      border: 1px solid var(--line);
      border-radius: 18px;
      padding: 14px;
      background: rgba(255,255,255,0.62);
    }}
    .flow-step span {{
      display: block;
      color: var(--muted);
      font-size: 0.76rem;
      text-transform: uppercase;
      letter-spacing: 0.08em;
    }}
    .flow-step strong {{
      display: block;
      margin-top: 6px;
      font-size: 1.08rem;
    }}
    .flow-step p {{
      margin: 8px 0 0;
      color: var(--muted);
      font-size: 0.94rem;
      line-height: 1.4;
    }}
    .flow-footer {{
      margin-top: 14px;
      padding-top: 14px;
      border-top: 1px solid rgba(216, 198, 180, 0.7);
      color: var(--muted);
      font-size: 0.95rem;
    }}
    .summary-grid {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 12px;
    }}
    .summary-chip {{
      border: 1px solid var(--line);
      border-radius: 18px;
      padding: 14px;
      background: rgba(255,255,255,0.55);
    }}
    .summary-chip span, .metric span, .metric-row span, .eyebrow {{
      display: block;
      color: var(--muted);
      font-size: 0.78rem;
      text-transform: uppercase;
      letter-spacing: 0.08em;
    }}
    .summary-chip strong, .metric strong {{ display: block; margin-top: 4px; font-size: 1.35rem; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(290px, 1fr)); gap: 16px; }}
    .card {{ display: flex; flex-direction: column; gap: 14px; }}
    .card-video {{
      width: 100%;
      aspect-ratio: 9 / 16;
      object-fit: cover;
      border-radius: 18px;
      background: #000;
      border: 1px solid rgba(32, 24, 21, 0.08);
    }}
    .placeholder {{
      display: grid;
      place-items: center;
      color: var(--muted);
      background: linear-gradient(180deg, #f0e5d7 0%, #eadbcc 100%);
    }}
    .card-top {{
      display: flex;
      align-items: start;
      justify-content: space-between;
      gap: 12px;
    }}
    .card h2 {{ margin: 4px 0 0; font-size: 2rem; line-height: 0.95; }}
    .metric-grid {{
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 10px;
    }}
    .metric {{
      border: 1px solid var(--line);
      border-radius: 16px;
      padding: 12px;
      background: rgba(255,255,255,0.6);
    }}
    .overlap-box {{
      border-radius: 18px;
      border: 1px solid var(--line);
      padding: 12px 14px;
      background: rgba(239, 224, 212, 0.32);
    }}
    .metric-row {{
      display: flex;
      justify-content: space-between;
      gap: 12px;
      padding: 6px 0;
      border-top: 1px solid rgba(216, 198, 180, 0.55);
    }}
    .metric-row:first-child {{ border-top: 0; padding-top: 0; }}
    .cta-row {{ display: flex; gap: 10px; }}
    .button {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      padding: 11px 14px;
      border-radius: 999px;
      border: 1px solid var(--line);
      text-decoration: none;
      color: var(--ink);
      background: rgba(255,255,255,0.72);
      font-weight: 700;
    }}
    .button-primary {{
      background: var(--accent);
      border-color: var(--accent);
      color: #fff8f2;
    }}
    .home-link {{
      margin-bottom: 14px;
    }}
    .pill {{
      padding: 7px 11px;
      border-radius: 999px;
      font-size: 0.76rem;
      font-weight: 700;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      border: 1px solid currentColor;
      white-space: nowrap;
    }}
    .pill-low {{ color: var(--ok); background: rgba(45, 106, 79, 0.10); }}
    .pill-medium {{ color: var(--warn); background: rgba(154, 107, 23, 0.10); }}
    .pill-high {{ color: var(--risk); background: rgba(157, 42, 31, 0.10); }}
    @media (max-width: 860px) {{
      .hero {{ grid-template-columns: 1fr; }}
      .metric-grid {{ grid-template-columns: 1fr; }}
      .summary-grid {{ grid-template-columns: 1fr 1fr; }}
      .hero-top {{ flex-direction: column; }}
      .flow-grid {{ grid-template-columns: 1fr 1fr; }}
    }}
    @media (max-width: 560px) {{
      .summary-grid {{ grid-template-columns: 1fr; }}
      .cta-row {{ flex-direction: column; }}
      .flow-grid {{ grid-template-columns: 1fr; }}
    }}
  </style>
</head>
<body>
  <main>
    <div class="home-link"><a class="button" href="/">Back to setup</a></div>
    <section class="hero">
      <article class="hero-card">
        <div class="hero-top">
          <div>
            <p class="eyebrow">Batch Review</p>
            <h1>Promo Batch</h1>
          </div>
          <button class="help-button" type="button" data-open-flow="batch-flow">How It Works</button>
        </div>
        <p>Review the exported soundtrack-backed concepts, compare overlap risk, and move directly into per-concept rating. The review action is the main path; play is there for quick spot checks.</p>
      </article>
      <aside class="summary-card">
        <div class="summary-grid">
          <div class="summary-chip">
            <span>Concepts</span>
            <strong>{concept_count}</strong>
          </div>
          <div class="summary-chip">
            <span>Avg Target</span>
            <strong>{avg_duration:.1f}s</strong>
          </div>
          <div class="summary-chip">
            <span>Avg Overlap</span>
            <strong>{avg_overlap:.2f}</strong>
          </div>
          <div class="summary-chip">
            <span>Highest Overlap</span>
            <strong>{escape(highest_overlap["concept_id"]) if highest_overlap else "n/a"}</strong>
          </div>
        </div>
      </aside>
    </section>
    <section class="grid">
      {''.join(cards)}
    </section>
  </main>
  <dialog id="batch-flow" class="flow-modal">
    <div class="flow-sheet">
      <div class="flow-top">
        <div>
          <p class="eyebrow">Flow</p>
          <h2>How Batch Review Works</h2>
          <p>This page is meant for fast triage. Use it to decide what deserves deeper review before you rerun the generator.</p>
        </div>
        <button class="close-button" type="button" data-close-flow="batch-flow">Close</button>
      </div>
      <div class="flow-grid">
        <div class="flow-step">
          <span>Step 1</span>
          <strong>Scan the batch</strong>
          <p>Use the preview cards to find strong candidates or concepts with high reuse risk.</p>
        </div>
        <div class="flow-step">
          <span>Step 2</span>
          <strong>Spot-check playback</strong>
          <p>Use <strong>Play MP4</strong> when you only need a quick pass on pacing, opener, or soundtrack fit.</p>
        </div>
        <div class="flow-step">
          <span>Step 3</span>
          <strong>Open full review</strong>
          <p>Use <strong>Open Review</strong> when you want to rate the concept or mark specific source files and trims.</p>
        </div>
        <div class="flow-step">
          <span>Step 4</span>
          <strong>Rerun with feedback</strong>
          <p>Source and trim feedback change the next run. Concept-level signals are stored for the future ranker.</p>
        </div>
      </div>
      <div class="flow-footer">
        Fast path: browse -> review only the concepts that matter -> rerun the generator after feedback.
      </div>
    </div>
  </dialog>
  <script>
    document.querySelectorAll("[data-open-flow]").forEach((button) => {{
      button.addEventListener("click", () => {{
        const dialog = document.getElementById(button.dataset.openFlow);
        if (dialog) dialog.showModal();
      }});
    }});
    document.querySelectorAll("[data-close-flow]").forEach((button) => {{
      button.addEventListener("click", () => {{
        const dialog = document.getElementById(button.dataset.closeFlow);
        if (dialog) dialog.close();
      }});
    }});
    document.querySelectorAll(".flow-modal").forEach((dialog) => {{
      dialog.addEventListener("click", (event) => {{
        const rect = dialog.getBoundingClientRect();
        const inside = rect.top <= event.clientY && event.clientY <= rect.bottom && rect.left <= event.clientX && event.clientX <= rect.right;
        if (!inside) dialog.close();
      }});
    }});
  </script>
</body>
</html>"""


def _review_html_v2() -> str:
    return """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>insta_autolayout review</title>
  <style>
    :root {
      --bg: #f5f6f8;
      --panel: #ffffff;
      --ink: #111827;
      --muted: #667085;
      --line: #d8dee8;
      --accent: #2454d6;
      --accent-soft: #e8efff;
      --ok: #19714b;
      --warn: #a15c00;
      --bad: #b42318;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background: var(--bg);
      color: var(--ink);
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }
    main {
      max-width: 1320px;
      margin: 0 auto;
      padding: 18px;
    }
    a { color: var(--accent); }
    .topbar {
      display: flex;
      justify-content: space-between;
      gap: 16px;
      align-items: center;
      margin-bottom: 14px;
    }
    .topbar h1 {
      margin: 0;
      font-size: 22px;
      line-height: 1.15;
      letter-spacing: 0;
    }
    .meta {
      color: var(--muted);
      font-size: 13px;
      line-height: 1.45;
    }
    .target-help {
      margin: -2px 0 10px;
      color: var(--muted);
      font-size: 13px;
      line-height: 1.4;
    }
    .nav {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      align-items: center;
    }
    .button, button {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-height: 36px;
      padding: 0 12px;
      border: 1px solid #c8d1df;
      border-radius: 7px;
      background: #fff;
      color: var(--ink);
      font: inherit;
      font-size: 13px;
      font-weight: 650;
      text-decoration: none;
      cursor: pointer;
    }
    button.primary {
      border-color: var(--accent);
      background: var(--accent);
      color: #fff;
    }
    .layout {
      display: grid;
      grid-template-columns: minmax(0, 1fr) 380px;
      gap: 14px;
      align-items: start;
    }
    .panel {
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--panel);
      padding: 14px;
    }
    .player-panel {
      padding: 0;
      overflow: hidden;
    }
    video {
      display: block;
      width: 100%;
      max-height: min(76vh, 820px);
      background: #000;
    }
    .timeline {
      display: flex;
      gap: 3px;
      padding: 10px;
      border-top: 1px solid var(--line);
      background: #f8fafc;
    }
    .segment {
      min-width: 18px;
      height: 42px;
      border: 1px solid #c8d1df;
      border-radius: 6px;
      background: #fff;
      color: #344054;
      font-size: 11px;
      overflow: hidden;
    }
    .segment.active {
      border-color: var(--accent);
      background: var(--accent-soft);
      color: var(--accent);
      box-shadow: inset 0 -3px 0 var(--accent);
    }
    .segment span {
      display: block;
      padding: 5px 6px;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }
    .current {
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 10px;
      padding: 12px 14px 14px;
      border-top: 1px solid var(--line);
    }
    .fact {
      min-width: 0;
      border: 1px solid #e0e6ef;
      border-radius: 7px;
      padding: 9px;
      background: #fff;
    }
    .fact span {
      display: block;
      color: var(--muted);
      font-size: 11px;
      text-transform: uppercase;
      letter-spacing: .05em;
    }
    .fact strong {
      display: block;
      margin-top: 4px;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      font-size: 13px;
    }
    .side {
      display: grid;
      gap: 14px;
    }
    .side h2 {
      margin: 0 0 10px;
      font-size: 15px;
    }
    .status-grid, .target-grid, .rating-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 7px;
    }
    .choice, .tag {
      display: flex;
      align-items: center;
      gap: 7px;
      min-height: 34px;
      padding: 7px 9px;
      border: 1px solid #d4dbe7;
      border-radius: 7px;
      background: #fff;
      font-size: 13px;
      cursor: pointer;
    }
    .choice.disabled {
      opacity: .45;
      cursor: not-allowed;
    }
    .tag {
      background: #f8fafc;
    }
    .tags {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 7px;
    }
    textarea {
      width: 100%;
      min-height: 80px;
      resize: vertical;
      border: 1px solid #c8d1df;
      border-radius: 7px;
      padding: 9px;
      font: inherit;
      font-size: 13px;
    }
    .status-line {
      min-height: 20px;
      color: var(--muted);
      font-size: 13px;
      font-weight: 650;
    }
    .quick {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 8px;
    }
    @media (max-width: 980px) {
      .layout { grid-template-columns: 1fr; }
      .current { grid-template-columns: repeat(2, minmax(0, 1fr)); }
    }
    @media (max-width: 620px) {
      main { padding: 12px; }
      .topbar { align-items: flex-start; flex-direction: column; }
      .current, .status-grid, .target-grid, .tags, .quick { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <main>
    <div id="app">Loading review...</div>
  </main>
  <script>
    const params = new URLSearchParams(window.location.search);
    let conceptId = params.get("concept");
    const app = document.getElementById("app");
    const reviewerId = localStorage.getItem("insta_autolayout.reviewer_id") || "rami";
    const projectId = localStorage.getItem("insta_autolayout.project_id") || "trybe";
    const statusOptions = ["unreviewed", "shortlist", "approved", "needs_edit", "reject"];
    const targetHelp = {
      concept: "Use this when your feedback is about the whole video: pacing, story, hook, music fit, or whether it is post-worthy.",
      source_file: "Use this when the current item itself should be preferred, avoided, or marked as overused/off-brand.",
      clip: "Use this when the exact current trim is good or bad, or its start/end/crop needs adjustment.",
      brand_card: "Use this when the intro or outro card itself needs work."
    };
    const reasonTagsByTarget = {
      concept: ["strong_hook", "weak_hook", "good_pacing", "bad_pacing", "repetitive", "postworthy", "bad_music_fit", "good_music_fit", "needs_clearer_offer", "too_long", "too_short"],
      source_file: ["source_high_quality", "source_overused", "off_brand", "too_shaky", "too_slow", "good_action", "prefer_more_like_this", "avoid_more_like_this"],
      clip: ["good_trim", "bad_trim", "starts_too_early", "starts_too_late", "ends_too_early", "ends_too_late", "bad_crop", "good_crop", "good_action", "too_shaky", "too_slow"],
      brand_card: ["strong_offer", "weak_offer", "too_long", "too_short", "hard_to_read", "good_brand_fit", "bad_brand_fit"]
    };

    const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
    }[char]));

    function clipToken(item) {
      const fileName = String(item.source_file || "").split("/").pop();
      return `${fileName}@${Number(item.source_start || 0).toFixed(1)}-${Number(item.source_end || 0).toFixed(1)}`;
    }

    function fileName(path) {
      return String(path || "").split("/").pop() || "unknown";
    }

    function timeRange(start, end) {
      return `${Number(start || 0).toFixed(1)}-${Number(end || 0).toFixed(1)}s`;
    }

    async function loadJson(path) {
      const response = await fetch(path);
      if (!response.ok) throw new Error(`${path}: ${response.status}`);
      return response.json();
    }

    async function postStructured(payload) {
      let response = await fetch("/api/review/events", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      });
      if (response.ok) return response.json();

      const action = fallbackAction(payload);
      response = await fetch("/api/feedback", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          concept_id: payload.concept_id,
          target_type: payload.target.type,
          action,
          source_file: payload.target.source_file || null,
          clip_token: payload.target.clip_token || null,
          note: payload.note || null
        })
      });
      return response.json();
    }

    async function postReviewClear(payload) {
      const response = await fetch("/api/review/clear", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      });
      const data = await response.json();
      if (!response.ok || data.ok === false) {
        throw new Error(data.error || `/api/review/clear returned ${response.status}`);
      }
      return data;
    }

    function fallbackAction(payload) {
      if (payload.target.type === "source_file") {
        return payload.status === "reject" ? "avoid_source" : "prefer_source";
      }
      if (payload.target.type === "clip") {
        return payload.status === "reject" || payload.status === "needs_edit" ? "bad_clip" : "good_clip";
      }
      return payload.status === "reject" ? "dislike_video" : "like_video";
    }

    function activeClipAt(timeline, time) {
      return timeline.find((item) => time >= Number(item.timeline_start || 0) && time < Number(item.timeline_end || 0)) || timeline[timeline.length - 1];
    }

    function renderRadios(name, values, current, labels = {}) {
      return values.map((value) => `
        <label class="choice">
          <input type="radio" name="${name}" value="${value}" ${value === current ? "checked" : ""}>
          <span class="choice-label" data-choice-label="${name}:${value}">${esc(labels[value] || value.replaceAll("_", " "))}</span>
        </label>
      `).join("");
    }

    function renderTags(targetType) {
      return (reasonTagsByTarget[targetType] || []).map((tag) => `
        <label class="tag">
          <input type="checkbox" name="reason_tags" value="${tag}">
          ${esc(tag.replaceAll("_", " "))}
        </label>
      `).join("");
    }

    function reviewTimeline(report) {
      const raw = report.timeline || [];
      const cards = report.brand_cards || {};
      const introDuration = cards.intro ? Number(cards.intro_duration || 1.1) : 0;
      const outroDuration = cards.outro ? Number(cards.outro_duration || 1.35) : 0;
      const items = [];
      if (introDuration > 0) {
        items.push({
          review_type: "brand_card",
          card_role: "intro",
          label: "Intro card",
          timeline_start: 0,
          timeline_end: introDuration,
          duration: introDuration,
          score_total: null,
          crop_strategy: "brand_card"
        });
      }
      for (const item of raw) {
        items.push({
          ...item,
          review_type: "clip",
          timeline_start: Number(item.timeline_start || 0) + introDuration,
          timeline_end: Number(item.timeline_end || 0) + introDuration
        });
      }
      const contentEnd = introDuration + Math.max(0, ...raw.map((item) => Number(item.timeline_end || 0)));
      if (outroDuration > 0) {
        items.push({
          review_type: "brand_card",
          card_role: "outro",
          label: "Outro card",
          timeline_start: contentEnd,
          timeline_end: contentEnd + outroDuration,
          duration: outroDuration,
          score_total: null,
          crop_strategy: "brand_card"
        });
      }
      return items;
    }

    function generationContext(item, report) {
      return {
        style: report.style,
        strategy: report.strategy,
        score_total: item ? item.score_total : null,
        boundary_confidence: item?.score_breakdown?.boundary_confidence ?? null,
        crop_strategy: item ? item.crop_strategy : null,
        source_type: item ? item.source_type : null,
        playback_rate: item ? item.playback_rate : null
      };
    }

    function targetPayload(type, currentClip) {
      if (type === "concept" || !currentClip) return { type: "concept" };
      if (currentClip.review_type === "brand_card") {
        return { type: "brand_card", role: currentClip.card_role || "brand_card" };
      }
      if (type === "source_file") {
        return { type, source_file: currentClip.source_file };
      }
      return {
        type: "clip",
        source_file: currentClip.source_file,
        candidate_id: currentClip.candidate_id,
        clip_token: clipToken(currentClip),
        timeline_start: currentClip.timeline_start,
        timeline_end: currentClip.timeline_end,
        source_start: currentClip.source_start,
        source_end: currentClip.source_end
      };
    }

    async function load() {
      const manifest = await loadJson("batch_manifest.json");
      if (!conceptId && manifest[0]) {
        conceptId = manifest[0].concept_id;
        history.replaceState(null, "", `review.html?concept=${encodeURIComponent(conceptId)}`);
      }
      const record = manifest.find((item) => item.concept_id === conceptId);
      const conceptIndex = manifest.findIndex((item) => item.concept_id === conceptId);
      if (!record) throw new Error(`Concept not found: ${conceptId}`);
      const report = await loadJson(`${conceptId}/report.json`);
      const variant = record.variants?.[0] || {};
      const timeline = reviewTimeline(report);
      const mediaClipCount = (report.timeline || []).length;
      let currentClip = timeline[0] || null;
      const previous = conceptIndex > 0 ? manifest[conceptIndex - 1] : null;
      const next = conceptIndex < manifest.length - 1 ? manifest[conceptIndex + 1] : null;
      const totalDuration = Math.max(...timeline.map((item) => Number(item.timeline_end || 0)), Number(record.target_duration || 1), 1);

      app.innerHTML = `
        <div class="topbar">
          <div>
            <div class="meta"><a href="/">Back to setup</a> / <a href="index.html">Back to batch</a> / ${esc(projectId)} / reviewer ${esc(reviewerId)}</div>
            <h1>${esc(record.concept_id)} - ${esc(String(record.strategy || "").replaceAll("_", " "))}</h1>
            <div class="meta">${mediaClipCount} media clips + ${timeline.length - mediaClipCount} brand cards / ${Number(totalDuration || 0).toFixed(1)}s review timeline / ${esc(variant.display_name || "variant")}</div>
          </div>
          <nav class="nav">
            ${previous ? `<a class="button" href="review.html?concept=${previous.concept_id}">Previous</a>` : ""}
            ${next ? `<a class="button" href="review.html?concept=${next.concept_id}">Next</a>` : ""}
          </nav>
        </div>
        <div class="layout">
          <section class="player-panel panel">
            <video id="player" controls playsinline src="${esc(variant.path || "")}"></video>
            <div id="timeline" class="timeline">
              ${timeline.map((item, index) => `
                <button class="segment" type="button" data-index="${index}" onclick="window.selectSegment && window.selectSegment(${index})" style="flex: ${Math.max(Number(item.duration || 0.2), 0.2)} 1 0">
                  <span>${index + 1}. ${esc(item.label || fileName(item.source_file))}</span>
                </button>
              `).join("")}
            </div>
            <div class="current">
              <div class="fact"><span>Current source</span><strong id="current-source"></strong></div>
              <div class="fact"><span>Timeline</span><strong id="current-timeline"></strong></div>
              <div class="fact"><span>Source trim</span><strong id="current-trim"></strong></div>
              <div class="fact"><span>Score / crop</span><strong id="current-score"></strong></div>
            </div>
          </section>
          <aside class="side">
            <section class="panel">
              <h2>What are you reviewing?</h2>
              <p id="target-help" class="target-help"></p>
              <div class="target-grid">${renderRadios("target_type", ["source_file", "clip", "concept"], "source_file", {concept: "whole video", source_file: "current item", clip: "current clip"})}</div>
            </section>
            <section class="panel">
              <h2>Rating</h2>
              <div class="rating-grid">${renderRadios("rating", ["-2", "-1", "0", "1", "2"], "0")}</div>
            </section>
            <section class="panel">
              <details id="detailed-feedback">
                <summary>Detailed feedback (optional)</summary>
                <div style="margin-top: 12px">
                  <h2>Status</h2>
                  <div class="status-grid">${renderRadios("status", statusOptions, "unreviewed")}</div>
                </div>
                <div style="margin-top: 12px">
                  <h2>Reason Tags</h2>
                  <div id="reason-tags" class="tags"></div>
                </div>
                <div style="margin-top: 12px">
                  <h2>Note</h2>
                  <textarea id="note" placeholder="Optional note for this concept, source, or exact trim"></textarea>
                </div>
                <div class="quick" style="margin-top: 10px">
                  <button type="button" data-quick="approved">Approve</button>
                  <button type="button" data-quick="shortlist">Shortlist</button>
                  <button type="button" data-quick="needs_edit">Needs edit</button>
                  <button type="button" data-quick="reject">Reject</button>
                </div>
              </details>
              <div style="margin-top: 10px">
                <button id="save" class="primary" type="button">Save Feedback</button>
                <button id="clear-form" type="button">Reset Form</button>
                <button id="clear-batch-feedback" type="button">Clear My Batch Feedback</button>
              </div>
              <p id="save-status" class="status-line"></p>
            </section>
          </aside>
        </div>
      `;

      const player = document.getElementById("player");
      const segments = [...document.querySelectorAll(".segment")];
      const saveStatus = document.getElementById("save-status");
      const reasonTagNode = document.getElementById("reason-tags");
      const targetHelpNode = document.getElementById("target-help");
      const noteNode = document.getElementById("note");
      const detailedNode = document.getElementById("detailed-feedback");
      let autosaveTimer = null;
      let saveInFlight = false;
      let pendingAutosave = false;
      let activeTargetKey = null;
      let suppressFormEvents = false;
      const draftStates = new Map();
      const savedSignatures = new Map();

      function selectedTargetType() {
        return document.querySelector('input[name="target_type"]:checked').value;
      }

      function setTargetType(type) {
        const input = document.querySelector(`input[name="target_type"][value="${type}"]`);
        if (input && !input.disabled) input.checked = true;
        updateTargetControls();
      }

      function updateTargetControls() {
        let targetType = selectedTargetType();
        const sourceInput = document.querySelector('input[name="target_type"][value="source_file"]');
        const clipInput = document.querySelector('input[name="target_type"][value="clip"]');
        const sourceLabel = document.querySelector('[data-choice-label="target_type:source_file"]');
        const sourceUnavailable = !currentClip?.source_file;
        const clipUnavailable = !currentClip?.source_file || currentClip?.review_type === "brand_card";
        if (sourceLabel) {
          sourceLabel.textContent = currentClip?.review_type === "brand_card" ? "current brand card" : "current source";
        }
        sourceInput.disabled = false;
        clipInput.disabled = clipUnavailable;
        sourceInput.closest(".choice").classList.toggle("disabled", false);
        clipInput.closest(".choice").classList.toggle("disabled", clipUnavailable);
        if (currentClip?.review_type === "brand_card") {
          clipInput.disabled = true;
          clipInput.closest(".choice").classList.add("disabled");
          if (targetType === "clip") {
            targetType = "source_file";
          }
        }
        if (targetType === "brand_card") {
          targetType = "source_file";
          sourceInput.checked = true;
        }
        const effectiveTargetType = currentClip?.review_type === "brand_card" && targetType === "source_file" ? "brand_card" : targetType;
        targetHelpNode.textContent = targetHelp[effectiveTargetType] || "";
        reasonTagNode.innerHTML = renderTags(effectiveTargetType);
      }

      function currentFormState() {
        return {
          targetType: selectedTargetType(),
          status: document.querySelector('input[name="status"]:checked').value,
          rating: Number(document.querySelector('input[name="rating"]:checked').value),
          tags: [...document.querySelectorAll('input[name="reason_tags"]:checked')].map((input) => input.value),
          note: noteNode.value || "",
          detailsOpen: detailedNode?.open || false
        };
      }

      function defaultFormState() {
        return { targetType: selectedTargetType(), status: "unreviewed", rating: 0, tags: [], note: "", detailsOpen: false };
      }

      function targetKeyForCurrentSelection() {
        const target = targetPayload(selectedTargetType(), currentClip);
        if (target.type === "concept") return `concept:${conceptId}`;
        if (target.type === "brand_card") return `brand_card:${conceptId}:${target.role || "brand_card"}`;
        if (target.type === "source_file") return `source_file:${conceptId}:${target.source_file || "unknown"}`;
        return `clip:${conceptId}:${target.clip_token || "unknown"}`;
      }

      function applyFormState(formState) {
        suppressFormEvents = true;
        try {
          const statusValue = String(formState?.status || "unreviewed");
          const ratingValue = String(Number(formState?.rating ?? 0));
          const tags = new Set(formState?.tags || []);
          const statusInput = document.querySelector(`input[name="status"][value="${statusValue}"]`);
          const ratingInput = document.querySelector(`input[name="rating"][value="${ratingValue}"]`);
          (statusInput || document.querySelector('input[name="status"][value="unreviewed"]')).checked = true;
          (ratingInput || document.querySelector('input[name="rating"][value="0"]')).checked = true;
          document.querySelectorAll('input[name="reason_tags"]').forEach((input) => {
            input.checked = tags.has(input.value);
          });
          noteNode.value = formState?.note || "";
          if (detailedNode) detailedNode.open = Boolean(formState?.detailsOpen);
        } finally {
          suppressFormEvents = false;
        }
      }

      function storeActiveDraft() {
        if (!activeTargetKey) return;
        draftStates.set(activeTargetKey, currentFormState());
      }

      function loadDraftForCurrentTarget() {
        activeTargetKey = targetKeyForCurrentSelection();
        const draft = draftStates.get(activeTargetKey) || defaultFormState();
        applyFormState(draft);
        if (!isMeaningfulState(draft)) {
          saveStatus.textContent = savedSignatures.has(activeTargetKey)
            ? "Saved feedback exists for this item."
            : "No saved feedback for this item yet.";
        }
      }

      function isMeaningfulState(formState) {
        return (
          formState.rating !== 0 ||
          formState.tags.length > 0 ||
          formState.note.trim() !== ""
        );
      }

      function inferredStatusFromRating(rating) {
        if (rating >= 1) return "approved";
        if (rating <= -2) return "reject";
        if (rating < 0) return "needs_edit";
        return "unreviewed";
      }

      function buildPayload() {
        const formState = currentFormState();
        const effectiveStatus = formState.status !== "unreviewed"
          ? formState.status
          : inferredStatusFromRating(formState.rating);
        return {
          reviewer_id: reviewerId,
          project_id: projectId,
          batch_id: null,
          concept_id: conceptId,
          variant_id: variant.audio_mode || variant.display_name || null,
          target: targetPayload(formState.targetType, currentClip),
          status: effectiveStatus,
          rating: formState.rating,
          reason_tags: formState.detailsOpen ? formState.tags : [],
          note: formState.detailsOpen ? (formState.note || null) : null,
          generation_context: generationContext(currentClip, report)
        };
      }

      async function performSave({ force = false } = {}) {
        if (autosaveTimer) {
          clearTimeout(autosaveTimer);
          autosaveTimer = null;
        }
        const formState = currentFormState();
        const targetKey = targetKeyForCurrentSelection();
        storeActiveDraft();
        if (!force && !isMeaningfulState(formState)) {
          saveStatus.textContent = savedSignatures.has(targetKey)
            ? "Saved feedback exists for this item."
            : "No saved feedback for this item yet.";
          return;
        }
        const payload = buildPayload();
        const signature = JSON.stringify(payload);
        if (!force && signature === savedSignatures.get(targetKey)) {
          saveStatus.textContent = "Saved.";
          return;
        }
        if (saveInFlight) {
          pendingAutosave = true;
          return;
        }
        saveInFlight = true;
        saveStatus.textContent = force ? "Saving..." : "Autosaving...";
        try {
          const result = await postStructured(payload);
          savedSignatures.set(targetKey, signature);
          saveStatus.textContent = result.ok
            ? `Saved. Events: ${result.summary?.structured_event_count || result.summary?.event_count || "ok"}`
            : "Save failed";
        } catch (error) {
          saveStatus.textContent = `Save failed: ${error}`;
        } finally {
          saveInFlight = false;
          if (pendingAutosave) {
            pendingAutosave = false;
            requestAutoSave(80);
          }
        }
      }

      function requestAutoSave(delay = 250) {
        if (autosaveTimer) clearTimeout(autosaveTimer);
        autosaveTimer = setTimeout(() => {
          performSave({ force: false }).catch((error) => {
            saveStatus.textContent = `Save failed: ${error}`;
          });
        }, delay);
      }

      function setCurrent(item) {
        storeActiveDraft();
        currentClip = item || currentClip;
        segments.forEach((segment) => segment.classList.toggle("active", Number(segment.dataset.index) === timeline.indexOf(currentClip)));
        document.getElementById("current-source").textContent = currentClip?.label || fileName(currentClip?.source_file);
        document.getElementById("current-timeline").textContent = currentClip ? timeRange(currentClip.timeline_start, currentClip.timeline_end) : "n/a";
        document.getElementById("current-trim").textContent = currentClip?.source_file ? timeRange(currentClip.source_start, currentClip.source_end) : (currentClip?.card_role || "n/a");
        document.getElementById("current-score").textContent = currentClip?.source_file ? `${Number(currentClip.score_total || 0).toFixed(2)} / ${currentClip.crop_strategy || "crop"}` : "brand card";
        updateTargetControls();
        loadDraftForCurrentTarget();
      }
      window.selectSegment = (index) => {
        const item = timeline[Number(index)];
        if (!item) return;
        player.currentTime = Number(item.timeline_start || 0);
        setCurrent(item);
      };

      player.addEventListener("timeupdate", () => setCurrent(activeClipAt(timeline, player.currentTime)));
      segments.forEach((segment) => {
        segment.addEventListener("click", () => {
          window.selectSegment(Number(segment.dataset.index));
        });
      });

      document.querySelectorAll("[data-quick]").forEach((button) => {
        button.addEventListener("click", () => {
          document.querySelector(`input[name="status"][value="${button.dataset.quick}"]`).checked = true;
          if (!suppressFormEvents) storeActiveDraft();
          requestAutoSave(120);
        });
      });
      document.querySelectorAll('input[name="target_type"]').forEach((input) => {
        input.addEventListener("change", () => {
          storeActiveDraft();
          updateTargetControls();
          loadDraftForCurrentTarget();
        });
      });
      document.querySelectorAll('input[name="status"]').forEach((input) => {
        input.addEventListener("change", () => {
          if (suppressFormEvents) return;
          storeActiveDraft();
          requestAutoSave(120);
        });
      });
      document.querySelectorAll('input[name="rating"]').forEach((input) => {
        input.addEventListener("change", () => {
          if (suppressFormEvents) return;
          storeActiveDraft();
          requestAutoSave(120);
        });
      });
      reasonTagNode.addEventListener("change", () => {
        if (suppressFormEvents) return;
        storeActiveDraft();
        requestAutoSave(150);
      });
      noteNode.addEventListener("input", () => {
        if (suppressFormEvents) return;
        storeActiveDraft();
        requestAutoSave(700);
      });
      noteNode.addEventListener("blur", () => {
        if (suppressFormEvents) return;
        storeActiveDraft();
        performSave({ force: false }).catch((error) => {
          saveStatus.textContent = `Save failed: ${error}`;
        });
      });
      detailedNode?.addEventListener("toggle", () => {
        if (suppressFormEvents) return;
        storeActiveDraft();
        if (!detailedNode.open) {
          requestAutoSave(120);
        }
      });

      document.getElementById("save").addEventListener("click", async () => {
        await performSave({ force: true });
      });

      document.getElementById("clear-form").addEventListener("click", () => {
        document.querySelector('input[name="status"][value="unreviewed"]').checked = true;
        document.querySelector('input[name="rating"][value="0"]').checked = true;
        document.querySelectorAll('input[name="reason_tags"]:checked').forEach((input) => { input.checked = false; });
        noteNode.value = "";
        draftStates.set(activeTargetKey, defaultFormState());
        updateTargetControls();
        loadDraftForCurrentTarget();
        if (autosaveTimer) {
          clearTimeout(autosaveTimer);
          autosaveTimer = null;
        }
        saveStatus.textContent = "Form reset. No saved events were deleted.";
      });

      document.getElementById("clear-batch-feedback").addEventListener("click", async () => {
        if (!window.confirm(`Delete all saved review events for reviewer ${reviewerId} in this batch?`)) return;
        saveStatus.textContent = "Clearing saved feedback...";
        try {
          const result = await postReviewClear({
            reviewer_id: reviewerId,
            project_id: projectId
          });
          savedSignatures.clear();
          draftStates.clear();
          loadDraftForCurrentTarget();
          saveStatus.textContent = `Cleared feedback. Remaining batch events: ${result.summary?.event_count ?? 0}`;
        } catch (error) {
          saveStatus.textContent = `Clear failed: ${error}`;
        }
      });

      setCurrent(currentClip);
    }

    load().catch((error) => {
      app.innerHTML = `<section class="panel"><strong>Review failed to load.</strong><p class="meta">${esc(error)}</p></section>`;
    });
  </script>
</body>
</html>"""


def _review_html() -> str:
    return """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Structured Review V2</title>
  <style>
    :root {
      --bg: #f6f7f8;
      --panel: #ffffff;
      --ink: #171a1f;
      --muted: #5f6875;
      --line: #d9dee5;
      --soft: #eef2f6;
      --accent: #1f6feb;
      --accent-soft: #e8f1ff;
      --ok: #18794e;
      --warn: #9a6700;
      --bad: #b42318;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      color: var(--ink);
      background: var(--bg);
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }
    main { max-width: 1480px; margin: 0 auto; padding: 24px 20px 56px; }
    a { color: var(--accent); text-decoration: none; }
    a:hover { text-decoration: underline; }
    h1, h2, h3, p { margin-top: 0; }
    h1 { margin-bottom: 4px; font-size: clamp(1.65rem, 2.4vw, 2.45rem); line-height: 1.05; }
    h2 { margin-bottom: 12px; font-size: 1rem; }
    h3 { margin-bottom: 8px; font-size: 0.92rem; }
    button {
      border: 1px solid var(--line);
      background: var(--panel);
      color: var(--ink);
      border-radius: 8px;
      padding: 9px 12px;
      cursor: pointer;
      font: inherit;
      font-weight: 700;
    }
    button:hover { border-color: #aab4c1; background: #f9fbfd; }
    button.is-active { border-color: var(--accent); background: var(--accent-soft); color: #0b4aa2; }
    .button-link {
      display: inline-flex;
      align-items: center;
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 9px 12px;
      color: var(--ink);
      background: var(--panel);
      font-weight: 700;
    }
    .button-primary { border-color: var(--accent); background: var(--accent); color: #fff; }
    .button-primary:hover { background: #185fcb; color: #fff; }
    .topline { display: flex; justify-content: space-between; gap: 18px; align-items: end; margin-bottom: 18px; }
    .meta { color: var(--muted); font-size: 0.92rem; line-height: 1.45; }
    .eyebrow, .label {
      display: block;
      color: var(--muted);
      font-size: 0.73rem;
      font-weight: 800;
      letter-spacing: 0.08em;
      margin-bottom: 4px;
      text-transform: uppercase;
    }
    .shell { display: grid; grid-template-columns: minmax(0, 1fr) 380px; gap: 16px; align-items: start; }
    .panel { background: var(--panel); border: 1px solid var(--line); border-radius: 8px; padding: 16px; }
    .player-panel { padding: 0; overflow: hidden; }
    video { display: block; width: 100%; min-height: min(72vh, 720px); max-height: 78vh; background: #050505; }
    .timeline {
      display: flex;
      gap: 2px;
      height: 56px;
      padding: 10px;
      border-top: 1px solid var(--line);
      background: #fbfcfd;
    }
    .segment {
      position: relative;
      min-width: 28px;
      height: 100%;
      border: 1px solid #c9d2dd;
      border-radius: 6px;
      background: var(--soft);
      overflow: hidden;
      text-align: left;
    }
    .segment.is-active { border-color: var(--accent); background: var(--accent-soft); box-shadow: inset 0 -3px 0 var(--accent); }
    .segment span {
      display: block;
      overflow: hidden;
      padding: 5px 6px;
      color: #2d3745;
      font-size: 0.72rem;
      font-weight: 800;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .content-grid { display: grid; grid-template-columns: minmax(0, 1fr) 380px; gap: 16px; margin-top: 16px; }
    .sticky { position: sticky; top: 16px; }
    .detail-list { display: grid; gap: 9px; margin: 0; }
    .detail-row { display: grid; grid-template-columns: 118px minmax(0, 1fr); gap: 10px; padding-top: 9px; border-top: 1px solid var(--soft); }
    .detail-row:first-child { border-top: 0; padding-top: 0; }
    .detail-row dt { color: var(--muted); font-size: 0.8rem; font-weight: 800; text-transform: uppercase; }
    .detail-row dd { margin: 0; min-width: 0; overflow-wrap: anywhere; }
    .detail-row code { font-size: 0.82rem; }
    .status-grid, .scope-grid, .reason-grid { display: flex; flex-wrap: wrap; gap: 8px; }
    .status-grid button[data-value="approved"].is-active,
    .status-grid button[data-value="shortlist"].is-active { border-color: var(--ok); background: #e9f7ef; color: var(--ok); }
    .status-grid button[data-value="needs_edit"].is-active { border-color: var(--warn); background: #fff7df; color: var(--warn); }
    .status-grid button[data-value="reject"].is-active { border-color: var(--bad); background: #ffebe9; color: var(--bad); }
    .reason-toggle {
      display: inline-flex;
      align-items: center;
      gap: 7px;
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 8px 10px;
      background: #fff;
      cursor: pointer;
      font-size: 0.9rem;
      font-weight: 700;
    }
    .reason-toggle input { margin: 0; }
    textarea {
      width: 100%;
      min-height: 110px;
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 10px;
      resize: vertical;
      font: inherit;
    }
    .form-stack { display: grid; gap: 16px; }
    .summary-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 10px; }
    .stat { border: 1px solid var(--line); border-radius: 8px; padding: 12px; background: #fbfcfd; }
    .stat strong { display: block; margin-top: 4px; font-size: 1.1rem; }
    .clip-strip { display: grid; gap: 8px; margin-top: 12px; }
    .clip-row {
      display: grid;
      grid-template-columns: 36px minmax(0, 1fr) auto;
      gap: 10px;
      align-items: center;
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 10px;
      background: #fff;
    }
    .clip-row.is-active { border-color: var(--accent); background: var(--accent-soft); }
    .clip-index { color: var(--muted); font-size: 0.82rem; font-weight: 800; }
    .clip-main { min-width: 0; }
    .clip-title { overflow: hidden; font-weight: 800; text-overflow: ellipsis; white-space: nowrap; }
    .clip-meta { color: var(--muted); font-size: 0.82rem; margin-top: 2px; }
    .save-row { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
    .status-line { min-height: 1.4em; color: var(--muted); font-size: 0.92rem; font-weight: 700; }
    .small-actions { display: flex; gap: 8px; flex-wrap: wrap; }
    @media (max-width: 1080px) {
      .shell, .content-grid { grid-template-columns: 1fr; }
      .sticky { position: static; }
      video { min-height: 360px; max-height: 72vh; }
    }
    @media (max-width: 620px) {
      main { padding: 16px 12px 40px; }
      .topline { align-items: start; flex-direction: column; }
      .summary-grid { grid-template-columns: 1fr 1fr; }
      .detail-row { grid-template-columns: 1fr; gap: 3px; }
      .clip-row { grid-template-columns: 28px minmax(0, 1fr); }
      .clip-row button { grid-column: 1 / -1; }
      .status-grid button, .scope-grid button { flex: 1 1 46%; }
    }
  </style>
</head>
<body>
  <main>
    <div id="app">Loading review...</div>
  </main>
  <script>
    const params = new URLSearchParams(window.location.search);
    const conceptId = params.get("concept");
    const app = document.getElementById("app");
    const reviewerId = localStorage.getItem("reviewer_id") || "rami";
    const projectId = localStorage.getItem("project_id") || "trybe";

    const STATUS_VALUES = ["unreviewed", "shortlist", "approved", "needs_edit", "reject"];
    const REASON_TAGS = [
      "strong_opening",
      "good_pacing",
      "good_source",
      "weak_source",
      "bad_trim",
      "crop_issue",
      "motion_issue",
      "boundary_issue",
      "repetitive",
      "audio_mismatch",
    ];

    const state = {
      activeIndex: 0,
      status: "unreviewed",
      scope: "concept",
      record: null,
      report: null,
      manifest: [],
      conceptIndex: -1,
    };

    function html(value) {
      return String(value ?? "").replace(/[&<>"']/g, (char) => ({
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#39;",
      })[char]);
    }

    function num(value, digits = 1, fallback = "n/a") {
      const parsed = Number(value);
      return Number.isFinite(parsed) ? parsed.toFixed(digits) : fallback;
    }

    function clipToken(item) {
      const fileName = String(item.source_file || "").split("/").pop();
      return `${fileName}@${num(item.source_start, 1, "0.0")}-${num(item.source_end, 1, "0.0")}`;
    }

    function fileName(path) {
      return String(path || "").split("/").pop() || "unknown";
    }

    function selectedReasons() {
      return [...document.querySelectorAll("[data-reason]:checked")].map((input) => input.value);
    }

    function activeClip() {
      return (state.report?.timeline || [])[state.activeIndex] || null;
    }

    function legacyAction(status, scope) {
      if (scope === "source") {
        if (status === "reject") return "avoid_source";
        return "prefer_source";
      }
      if (scope === "clip") {
        if (status === "reject" || status === "needs_edit") return "bad_clip";
        return "good_clip";
      }
      if (status === "approved") return "postworthy_video";
      if (status === "shortlist") return "like_video";
      if (status === "reject") return "dislike_video";
      if (status === "needs_edit") return "needs_edit_video";
      return "reviewed_video";
    }

    function buildStructuredPayload() {
      const clip = activeClip();
      const notes = document.getElementById("notes")?.value || "";
      const scope = state.scope;
      return {
        schema_version: "structured_review_v2",
        event_type: "review_feedback",
        project_id: projectId,
        reviewer_id: reviewerId,
        concept_id: conceptId,
        concept_index: state.conceptIndex,
        concept_strategy: state.record?.strategy || null,
        status: state.status,
        target_type: scope,
        target: {
          type: scope,
          source_file: scope === "source" || scope === "clip" ? clip?.source_file || null : null,
          source_filename: scope === "source" || scope === "clip" ? fileName(clip?.source_file) : null,
          clip_index: scope === "clip" ? state.activeIndex : null,
          clip_token: scope === "clip" && clip ? clipToken(clip) : null,
        },
        playback: {
          timeline_time: document.getElementById("player")?.currentTime || 0,
          active_clip_index: state.activeIndex,
        },
        source_trim: clip ? {
          start: Number(clip.source_start),
          end: Number(clip.source_end),
          duration: Number(clip.duration),
          playback_rate: Number(clip.playback_rate || 1),
        } : null,
        clip_metadata: clip ? {
          timeline_start: Number(clip.timeline_start),
          timeline_end: Number(clip.timeline_end),
          score: Number(clip.score_total),
          motion_treatment: clip.motion_treatment || null,
          boundary_type: clip.boundary_type || clip.transition_in || null,
          transition_in: clip.transition_in || null,
          transition_out: clip.transition_out || null,
          crop_strategy: clip.crop_strategy || null,
          why_selected: clip.why_selected || null,
        } : null,
        reason_tags: selectedReasons(),
        notes,
        created_at: new Date().toISOString(),
      };
    }

    function buildLegacyPayload(payload) {
      const clip = activeClip();
      return {
        concept_id: payload.concept_id,
        target_type: payload.target_type === "source" ? "source_file" : payload.target_type === "clip" ? "clip" : "concept",
        action: legacyAction(payload.status, payload.target_type),
        source_file: payload.target_type === "source" || payload.target_type === "clip" ? clip?.source_file || null : null,
        clip_token: payload.target_type === "clip" && clip ? clipToken(clip) : null,
        note: payload.notes || null,
        status: payload.status,
        reason_tags: payload.reason_tags,
        reviewer_id: payload.reviewer_id,
        project_id: payload.project_id,
      };
    }

    async function postJson(path, payload) {
      const response = await fetch(path, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      let data = {};
      try {
        data = await response.json();
      } catch {
        data = {};
      }
      if (!response.ok || data.ok === false) {
        throw new Error(data.error || `${path} returned ${response.status}`);
      }
      return data;
    }

    async function saveReview() {
      const statusNode = document.getElementById("save-status");
      const payload = buildStructuredPayload();
      statusNode.textContent = "Saving...";
      try {
        await postJson("/api/review/events", payload);
        statusNode.textContent = "Saved to structured review events.";
      } catch (error) {
        try {
          const legacy = await postJson("/api/feedback", buildLegacyPayload(payload));
          const count = legacy.summary?.event_count;
          statusNode.textContent = count ? `Saved through legacy feedback. Events: ${count}` : "Saved through legacy feedback.";
        } catch (fallbackError) {
          statusNode.textContent = `Save failed: ${fallbackError.message || fallbackError}`;
        }
      }
    }

    function setActiveIndex(index, seek = false) {
      const timeline = state.report?.timeline || [];
      if (!timeline.length) return;
      state.activeIndex = Math.max(0, Math.min(index, timeline.length - 1));
      const clip = timeline[state.activeIndex];
      document.querySelectorAll("[data-segment]").forEach((node) => {
        node.classList.toggle("is-active", Number(node.dataset.index) === state.activeIndex);
      });
      document.querySelectorAll("[data-clip-row]").forEach((node) => {
        node.classList.toggle("is-active", Number(node.dataset.index) === state.activeIndex);
      });
      renderCurrentClip(clip);
      if (seek) {
        const player = document.getElementById("player");
        if (player) player.currentTime = Number(clip.timeline_start || 0) + 0.01;
      }
    }

    function setStatus(value) {
      state.status = value;
      document.querySelectorAll("[data-status]").forEach((button) => {
        button.classList.toggle("is-active", button.dataset.value === value);
      });
    }

    function setScope(value) {
      state.scope = value;
      document.querySelectorAll("[data-scope]").forEach((button) => {
        button.classList.toggle("is-active", button.dataset.value === value);
      });
      renderCurrentClip(activeClip());
    }

    function clipAtTime(time) {
      const timeline = state.report?.timeline || [];
      const match = timeline.findIndex((item) => {
        const start = Number(item.timeline_start || 0);
        const end = Number(item.timeline_end || start + item.duration || 0);
        return time >= start && time < end;
      });
      if (match >= 0) return match;
      return Math.max(0, timeline.length - 1);
    }

    function renderCurrentClip(item) {
      const node = document.getElementById("current-clip");
      if (!node || !item) return;
      const boundary = item.boundary_type || [item.transition_in, item.transition_out].filter(Boolean).join(" / ") || "n/a";
      node.innerHTML = `
        <h2>Current Clip</h2>
        <dl class="detail-list">
          <div class="detail-row"><dt>Target</dt><dd>${html(state.scope)} feedback</dd></div>
          <div class="detail-row"><dt>Source</dt><dd><strong>${html(fileName(item.source_file))}</strong><br><span class="meta">${html(item.source_file)}</span></dd></div>
          <div class="detail-row"><dt>Timeline</dt><dd>${num(item.timeline_start)}-${num(item.timeline_end)}s</dd></div>
          <div class="detail-row"><dt>Source Trim</dt><dd>${num(item.source_start)}-${num(item.source_end)}s &middot; ${num(item.duration, 2)}s &middot; ${num(item.playback_rate, 2, "1.00")}x</dd></div>
          <div class="detail-row"><dt>Score</dt><dd>${num(item.score_total, 2)}</dd></div>
          <div class="detail-row"><dt>Motion</dt><dd>${html(item.motion_treatment || "n/a")}</dd></div>
          <div class="detail-row"><dt>Boundary</dt><dd>${html(boundary)}</dd></div>
          <div class="detail-row"><dt>Crop</dt><dd>${html(item.crop_strategy || "n/a")}</dd></div>
          <div class="detail-row"><dt>Clip Token</dt><dd><code>${html(clipToken(item))}</code></dd></div>
        </dl>
      `;
    }

    async function load() {
      if (!conceptId) {
        app.textContent = "Missing concept query parameter.";
        return;
      }
      const manifest = await fetch("batch_manifest.json").then((r) => r.json());
      const record = manifest.find((item) => item.concept_id === conceptId);
      const conceptIndex = manifest.findIndex((item) => item.concept_id === conceptId);
      if (!record) {
        app.textContent = `Concept not found: ${conceptId}`;
        return;
      }
      const report = await fetch(`${conceptId}/report.json`).then((r) => r.json());
      state.record = record;
      state.report = report;
      state.manifest = manifest;
      state.conceptIndex = conceptIndex;
      const autoVariant = record.variants[0];
      const uniqueSources = new Set(report.timeline.map((item) => item.source_file)).size;
      const primaryVariant = (report.variants || [])[0] || {};
      const previousRecord = conceptIndex > 0 ? manifest[conceptIndex - 1] : null;
      const nextRecord = conceptIndex < manifest.length - 1 ? manifest[conceptIndex + 1] : null;
      const totalDuration = report.timeline.reduce((total, item) => total + Math.max(Number(item.duration || 0), 0.01), 0);
      const segments = report.timeline.map((item, index) => {
        const width = Math.max(Number(item.duration || 0.01), 0.01) / Math.max(totalDuration, 0.01) * 100;
        return `<button class="segment" type="button" data-segment data-index="${index}" style="flex-basis: ${width}%"><span>${index + 1}. ${html(fileName(item.source_file))}</span></button>`;
      }).join("");
      const clipRows = report.timeline.map((item, index) => `
        <div class="clip-row" data-clip-row data-index="${index}">
          <div class="clip-index">${index + 1}</div>
          <div class="clip-main">
            <div class="clip-title">${html(fileName(item.source_file))}</div>
            <div class="clip-meta">${num(item.timeline_start)}-${num(item.timeline_end)}s &middot; score ${num(item.score_total, 2)} &middot; ${html(item.crop_strategy || "n/a")}</div>
          </div>
          <button type="button" data-jump data-index="${index}">Jump</button>
        </div>
      `).join("");
      const statusButtons = STATUS_VALUES.map((value) => `<button type="button" data-status data-value="${value}">${html(value.replace("_", " "))}</button>`).join("");
      const reasonControls = REASON_TAGS.map((tag) => `
        <label class="reason-toggle">
          <input type="checkbox" data-reason value="${html(tag)}" />
          <span>${html(tag.replaceAll("_", " "))}</span>
        </label>
      `).join("");

      app.innerHTML = `
        <div class="topline">
          <div>
            <p class="eyebrow">Concept ${conceptIndex + 1} / ${manifest.length}</p>
            <h1>${html(record.strategy.replaceAll("_", " "))}</h1>
            <p class="meta">${html(conceptId)} &middot; reviewer ${html(reviewerId)} &middot; project ${html(projectId)}</p>
          </div>
          <div class="small-actions">
            <a class="button-link" href="index.html">Back To Batch</a>
            ${previousRecord ? `<a class="button-link" href="review.html?concept=${html(previousRecord.concept_id)}">Previous</a>` : ""}
            ${nextRecord ? `<a class="button-link" href="review.html?concept=${html(nextRecord.concept_id)}">Next</a>` : ""}
          </div>
        </div>
        <div class="shell">
          <section class="player-panel panel">
            <video id="player" controls src="${html(autoVariant.path)}"></video>
            <div id="timeline" class="timeline">${segments}</div>
          </section>
          <aside id="current-clip" class="panel sticky"></aside>
        </div>
        <div class="content-grid">
          <section class="panel">
            <h2>Concept Summary</h2>
            <div class="summary-grid">
              <div class="stat"><span class="label">Target</span><strong>${num(record.target_duration)}s</strong></div>
              <div class="stat"><span class="label">Clips</span><strong>${report.timeline.length}</strong></div>
              <div class="stat"><span class="label">Sources</span><strong>${uniqueSources}</strong></div>
              <div class="stat"><span class="label">Overlap</span><strong>${num((record.overlap || {}).max_source_jaccard, 2)}</strong></div>
            </div>
            <p class="meta" style="margin-top: 12px;">${html(autoVariant.display_name || "")}${primaryVariant.track_title ? ` &middot; Track: ${html(primaryVariant.track_title)}` : ""}</p>
            <details>
              <summary>Generation notes</summary>
              <p class="meta">${html(report.why_this_version || "")}</p>
              <ul class="meta">${(report.diversity_notes || []).map((note) => `<li>${html(note)}</li>`).join("")}</ul>
            </details>
            <div class="clip-strip">${clipRows}</div>
          </section>
          <aside class="panel sticky">
            <form id="review-form" class="form-stack">
              <div>
                <h2>Feedback Scope</h2>
                <div class="scope-grid">
                  <button type="button" data-scope data-value="concept">Concept</button>
                  <button type="button" data-scope data-value="source">Source</button>
                  <button type="button" data-scope data-value="clip">Exact Clip</button>
                </div>
              </div>
              <div>
                <h2>Status</h2>
                <div class="status-grid">${statusButtons}</div>
              </div>
              <div>
                <h2>Reason Tags</h2>
                <div class="reason-grid">${reasonControls}</div>
              </div>
              <div>
                <h2>Notes</h2>
                <textarea id="notes" placeholder="Optional review notes"></textarea>
              </div>
              <div class="save-row">
                <button class="button-primary" type="submit">Save Review Event</button>
                <span id="save-status" class="status-line"></span>
              </div>
            </form>
          </aside>
        </div>
      `;

      document.querySelectorAll("[data-segment], [data-jump]").forEach((button) => {
        button.addEventListener("click", () => setActiveIndex(Number(button.dataset.index), true));
      });
      document.querySelectorAll("[data-status]").forEach((button) => {
        button.addEventListener("click", () => setStatus(button.dataset.value));
      });
      document.querySelectorAll("[data-scope]").forEach((button) => {
        button.addEventListener("click", () => setScope(button.dataset.value));
      });
      document.getElementById("review-form").addEventListener("submit", async (event) => {
        event.preventDefault();
        await saveReview();
      });
      document.getElementById("player").addEventListener("timeupdate", (event) => {
        const index = clipAtTime(event.currentTarget.currentTime);
        if (index !== state.activeIndex) setActiveIndex(index, false);
      });
      setStatus("unreviewed");
      setScope("concept");
      setActiveIndex(0, false);
      if (!report.timeline.length) {
        document.getElementById("current-clip").innerHTML = "<h2>Current Clip</h2><p class='meta'>No timeline clips in report.json.</p>";
        document.querySelector(".button-primary").disabled = true;
      }
    }

    load().catch((error) => {
      app.innerHTML = `<section class="panel">Review failed to load: ${html(error)}</section>`;
    });
  </script>
</body>
</html>"""


def _strategy_label(name: str) -> str:
    return " / ".join(part.replace("_", " ").title() for part in name.split("_", 1)) if "_" in name else name.replace("_", " ").title()


def _overlap_label(value: float) -> str:
    if value >= 0.45:
        return "high overlap"
    if value >= 0.22:
        return "medium overlap"
    return "low overlap"


def _overlap_class(value: float) -> str:
    if value >= 0.45:
        return "pill-high"
    if value >= 0.22:
        return "pill-medium"
    return "pill-low"


def _primary_bpm(label: str) -> str:
    for token in label.split():
        if token.isdigit():
            return f"{token} BPM"
    return label
