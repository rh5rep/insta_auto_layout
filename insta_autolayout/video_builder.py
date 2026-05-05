from __future__ import annotations

from pathlib import Path

from PIL import Image

from .models import MediaAsset, Plan
from .moviepy_compat import ColorClip, CompositeVideoClip, ImageClip, VideoFileClip, concatenate_videoclips
from .rendering import render_image_to_canvas


class ReelBuilder:
    def build(self, plan: Plan, assets_by_path: dict[str, MediaAsset], output_path: Path) -> None:
        if VideoFileClip is None or ImageClip is None or concatenate_videoclips is None:
            raise RuntimeError("moviepy is required to build reel exports")

        clips = []
        temp_images: list[Path] = []
        canvas_width, canvas_height = plan.output_canvas
        audio_source = None

        try:
            for slide in plan.slides:
                source = slide.source_files[0]
                asset = assets_by_path[source]
                if asset.media_type == "image":
                    with Image.open(asset.source_path) as image:
                        rendered = render_image_to_canvas(
                            image=image,
                            asset=asset,
                            canvas_size=plan.output_canvas,
                            strategy=slide.crop_strategy if slide.crop_strategy != "smart_crop_or_pad" else "smart_crop",
                        )
                    temp_image = output_path.parent / f".reel_frame_{slide.index:02d}.jpg"
                    rendered.save(temp_image, quality=95)
                    temp_images.append(temp_image)
                    clip = (
                        ImageClip(str(temp_image))
                        .with_duration(slide.duration or 2.2)
                        .resized(lambda t: 1.0 + 0.04 * (t / max(slide.duration or 2.2, 0.1)))
                        .with_position("center")
                    )
                    bg = ColorClip(size=(canvas_width, canvas_height), color=(0, 0, 0)).with_duration(clip.duration)
                    clips.append(CompositeVideoClip([bg, clip.with_position("center")], size=(canvas_width, canvas_height)))
                else:
                    clip = VideoFileClip(asset.source_path)
                    trimmed = clip.subclipped(0, min(slide.duration or clip.duration, clip.duration))
                    resized = _fit_video(trimmed, canvas_width, canvas_height)
                    clips.append(resized)
                    if audio_source is None and getattr(trimmed, "audio", None) is not None:
                        audio_source = trimmed.audio

            final = concatenate_videoclips(clips, method="compose")
            if audio_source is not None:
                final = final.with_audio(audio_source)
            final.write_videofile(str(output_path), fps=24, codec="libx264", audio_codec="aac")
        finally:
            for clip in clips:
                try:
                    clip.close()
                except Exception:
                    pass
            for temp_image in temp_images:
                temp_image.unlink(missing_ok=True)


def _fit_video(clip, width: int, height: int):
    ratio = clip.w / clip.h
    target_ratio = width / height
    if ratio > target_ratio:
        resized = clip.resized(height=height)
    else:
        resized = clip.resized(width=width)
    return resized.with_background_color(size=(width, height), color=(0, 0, 0), pos=("center", "center"))
