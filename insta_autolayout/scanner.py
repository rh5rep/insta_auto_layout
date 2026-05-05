from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

from .models import MediaAsset
from .moviepy_compat import VideoFileClip
from .rendering import average_hash, detect_faces_and_saliency, difference_hash, estimate_sharpness

try:  # pragma: no cover - optional codec support
    from pillow_heif import register_heif_opener
except Exception:  # pragma: no cover - environment-dependent
    register_heif_opener = None


if register_heif_opener is not None:
    register_heif_opener()


IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff", ".heic", ".heif"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm"}


@dataclass(slots=True)
class ScanOutcome:
    assets: list[MediaAsset]
    exclusions: list[dict[str, str]]


class MediaScanner:
    def scan(self, input_dir: Path) -> ScanOutcome:
        assets: list[MediaAsset] = []
        exclusions: list[dict[str, str]] = []

        for path in sorted(input_dir.rglob("*")):
            if not path.is_file():
                continue
            if path.name.startswith("."):
                continue
            suffix = path.suffix.lower()
            try:
                if suffix in IMAGE_EXTENSIONS:
                    assets.append(self._scan_image(path))
                elif suffix in VIDEO_EXTENSIONS:
                    assets.append(self._scan_video(path))
                else:
                    reason = "unsupported_file_type"
                    if suffix in {".heic", ".heif"} and register_heif_opener is None:
                        reason = "heic_requires_pillow_heif"
                    exclusions.append({"file": str(path), "reason": reason})
            except Exception as exc:
                if suffix in {".heic", ".heif"}:
                    exclusions.append({"file": str(path), "reason": f"heic_decode_failed: {exc}"})
                    continue
                exclusions.append({"file": str(path), "reason": f"scan_failed: {exc}"})

        return ScanOutcome(assets=assets, exclusions=exclusions)

    def _scan_image(self, path: Path) -> MediaAsset:
        with Image.open(path) as image:
            image = ImageOps.exif_transpose(image).convert("RGB")
            width, height = image.size
            sample = np.array(image)

        sharpness = estimate_sharpness(sample)
        face_boxes, salient_box, edge_risk = detect_faces_and_saliency(sample)
        asset = MediaAsset(
            source_path=str(path),
            file_name=path.name,
            media_type="image",
            width=width,
            height=height,
            duration=None,
            aspect_ratio=width / height,
            orientation=_orientation(width, height),
            sharpness=sharpness,
            average_hash=average_hash(sample),
            difference_hash=difference_hash(sample),
            mean_color=tuple(float(value) for value in sample.mean(axis=(0, 1))),
            face_boxes=face_boxes,
            salient_box=salient_box,
            edge_risk=edge_risk,
        )
        asset.analysis_notes.append(self._analysis_note(asset))
        return asset

    def _scan_video(self, path: Path) -> MediaAsset:
        if VideoFileClip is None:
            raise RuntimeError("moviepy is not installed or failed to import")

        with VideoFileClip(str(path), audio=False) as clip:
            width = int(clip.w)
            height = int(clip.h)
            duration = float(clip.duration or 0.0)
            sample_time = min(max(duration * 0.33, 0.0), max(duration - 0.1, 0.0))
            frame = clip.get_frame(sample_time if duration > 0 else 0)
            sample = np.array(frame).astype(np.uint8)

        sharpness = estimate_sharpness(sample)
        face_boxes, salient_box, edge_risk = detect_faces_and_saliency(sample)
        asset = MediaAsset(
            source_path=str(path),
            file_name=path.name,
            media_type="video",
            width=width,
            height=height,
            duration=duration,
            aspect_ratio=width / height,
            orientation=_orientation(width, height),
            sharpness=sharpness,
            average_hash=average_hash(sample),
            difference_hash=difference_hash(sample),
            mean_color=tuple(float(value) for value in sample.mean(axis=(0, 1))),
            face_boxes=face_boxes,
            salient_box=salient_box,
            edge_risk=edge_risk,
        )
        asset.analysis_notes.append(self._analysis_note(asset))
        if duration <= 12:
            asset.analysis_notes.append("short clip suitable for carousel slot or reel segment")
        return asset

    def _analysis_note(self, asset: MediaAsset) -> str:
        face_note = "faces detected" if asset.face_boxes else "no faces detected"
        return f"{asset.orientation} asset, sharpness {asset.sharpness:.1f}, {face_note}, edge risk {asset.edge_risk:.2f}"


def _orientation(width: int, height: int) -> str:
    if width == height:
        return "square"
    return "portrait" if height > width else "landscape"
