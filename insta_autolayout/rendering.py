from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import cv2
import numpy as np
from PIL import Image, ImageFilter, ImageOps

from .models import MediaAsset


@dataclass(slots=True)
class CropBox:
    left: int
    top: int
    right: int
    bottom: int


def compute_smart_crop(asset: MediaAsset, target_width: int, target_height: int) -> CropBox:
    source_ratio = asset.width / asset.height
    target_ratio = target_width / target_height
    keep_width = asset.width
    keep_height = asset.height

    if source_ratio > target_ratio:
        keep_width = int(round(asset.height * target_ratio))
    else:
        keep_height = int(round(asset.width / target_ratio))

    cx, cy = _protected_center(asset)
    half_width = keep_width / 2
    half_height = keep_height / 2

    left = int(round(cx - half_width))
    top = int(round(cy - half_height))
    left = max(0, min(left, asset.width - keep_width))
    top = max(0, min(top, asset.height - keep_height))

    # Expand the crop back toward protected content when a face or salient area
    # would otherwise get clipped. This keeps cropping explainable and stable.
    protected = list(_protected_boxes(asset))
    if protected:
        for box in protected:
            left = min(left, int(box["x"]))
            top = min(top, int(box["y"]))
        left = max(0, min(left, asset.width - keep_width))
        top = max(0, min(top, asset.height - keep_height))

    return CropBox(left=left, top=top, right=left + keep_width, bottom=top + keep_height)


def render_image_to_canvas(
    image: Image.Image,
    asset: MediaAsset,
    canvas_size: tuple[int, int],
    strategy: str,
    background_blur: bool = True,
) -> Image.Image:
    image = ImageOps.exif_transpose(image).convert("RGB")
    canvas_width, canvas_height = canvas_size
    if strategy == "pad":
        return pad_to_canvas(image, canvas_size, blur=background_blur)

    crop = compute_smart_crop(asset, canvas_width, canvas_height)
    cropped = image.crop((crop.left, crop.top, crop.right, crop.bottom))
    return cropped.resize(canvas_size, Image.Resampling.LANCZOS)


def pad_to_canvas(image: Image.Image, canvas_size: tuple[int, int], blur: bool = True) -> Image.Image:
    canvas_width, canvas_height = canvas_size
    fitted = ImageOps.contain(image, canvas_size, Image.Resampling.LANCZOS)
    if blur:
        background = ImageOps.fit(image, canvas_size, Image.Resampling.LANCZOS)
        background = background.filter(ImageFilter.GaussianBlur(radius=30))
        background = background.point(lambda value: int(value * 0.88))
    else:
        background = Image.new("RGB", canvas_size, color=(18, 18, 18))
    offset = ((canvas_width - fitted.width) // 2, (canvas_height - fitted.height) // 2)
    background.paste(fitted, offset)
    return background


def protected_crop_score(asset: MediaAsset, region_width: int, region_height: int) -> float:
    target_ratio = region_width / region_height
    source_ratio = asset.aspect_ratio
    ratio_penalty = min(abs(source_ratio - target_ratio) / max(target_ratio, 0.01), 1.0)
    safety_penalty = min(asset.edge_risk, 1.0)
    duplicate_penalty = min(asset.duplicate_score, 1.0) * 0.25
    return max(0.0, 1.0 - ratio_penalty - (0.55 * safety_penalty) - duplicate_penalty)


def detect_faces_and_saliency(rgb_image: np.ndarray) -> tuple[list[dict[str, float]], dict[str, float] | None, float]:
    gray = cv2.cvtColor(rgb_image, cv2.COLOR_RGB2GRAY)
    face_boxes: list[dict[str, float]] = []

    try:
        cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
        detected = cascade.detectMultiScale(gray, scaleFactor=1.12, minNeighbors=4, minSize=(40, 40))
        for x, y, w, h in detected:
            face_boxes.append({"x": float(x), "y": float(y), "w": float(w), "h": float(h)})
    except Exception:
        face_boxes = []

    salient_box = _find_salient_box(gray)
    edge_risk = _compute_edge_risk(gray.shape[1], gray.shape[0], face_boxes, salient_box)
    return face_boxes, salient_box, edge_risk


def estimate_sharpness(rgb_image: np.ndarray) -> float:
    gray = cv2.cvtColor(rgb_image, cv2.COLOR_RGB2GRAY)
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def average_hash(rgb_image: np.ndarray, hash_size: int = 8) -> str:
    gray = cv2.cvtColor(rgb_image, cv2.COLOR_RGB2GRAY)
    resized = cv2.resize(gray, (hash_size, hash_size), interpolation=cv2.INTER_AREA)
    mean_value = float(resized.mean())
    bits = "".join("1" if px >= mean_value else "0" for px in resized.flatten())
    return f"{int(bits, 2):0{hash_size * hash_size // 4}x}"


def difference_hash(rgb_image: np.ndarray, hash_size: int = 8) -> str:
    gray = cv2.cvtColor(rgb_image, cv2.COLOR_RGB2GRAY)
    resized = cv2.resize(gray, (hash_size + 1, hash_size), interpolation=cv2.INTER_AREA)
    diff = resized[:, 1:] >= resized[:, :-1]
    bits = "".join("1" if flag else "0" for flag in diff.flatten())
    return f"{int(bits, 2):0{hash_size * hash_size // 4}x}"


def hamming_distance(hash_a: str, hash_b: str) -> int:
    return bin(int(hash_a, 16) ^ int(hash_b, 16)).count("1")


def color_distance(color_a: tuple[float, float, float], color_b: tuple[float, float, float]) -> float:
    return float(np.linalg.norm(np.array(color_a, dtype=np.float32) - np.array(color_b, dtype=np.float32)))


def frame_to_pil(frame: np.ndarray) -> Image.Image:
    return Image.fromarray(frame.astype(np.uint8), mode="RGB")


def _protected_center(asset: MediaAsset) -> tuple[float, float]:
    boxes = list(_protected_boxes(asset))
    if not boxes:
        return asset.width / 2, asset.height / 2

    total_weight = 0.0
    weighted_x = 0.0
    weighted_y = 0.0
    for box in boxes:
        area = max(box["w"] * box["h"], 1.0)
        weighted_x += (box["x"] + box["w"] / 2) * area
        weighted_y += (box["y"] + box["h"] / 2) * area
        total_weight += area
    return weighted_x / total_weight, weighted_y / total_weight


def _protected_boxes(asset: MediaAsset) -> Iterable[dict[str, float]]:
    for box in asset.face_boxes:
        yield box
    if asset.salient_box:
        yield asset.salient_box


def _find_salient_box(gray: np.ndarray) -> dict[str, float] | None:
    blurred = cv2.GaussianBlur(gray, (0, 0), sigmaX=2.2)
    gradient_x = cv2.Sobel(blurred, cv2.CV_32F, 1, 0, ksize=3)
    gradient_y = cv2.Sobel(blurred, cv2.CV_32F, 0, 1, ksize=3)
    magnitude = cv2.magnitude(gradient_x, gradient_y)
    if float(magnitude.max()) <= 1.0:
        return None

    threshold = np.percentile(magnitude, 87)
    mask = magnitude >= threshold
    points = np.argwhere(mask)
    if len(points) < 20:
        return None
    y0, x0 = points.min(axis=0)
    y1, x1 = points.max(axis=0)
    return {
        "x": float(x0),
        "y": float(y0),
        "w": float(max(1, x1 - x0)),
        "h": float(max(1, y1 - y0)),
    }


def _compute_edge_risk(
    width: int,
    height: int,
    face_boxes: list[dict[str, float]],
    salient_box: dict[str, float] | None,
) -> float:
    margin_x = width * 0.12
    margin_y = height * 0.12
    boxes = list(face_boxes)
    if salient_box:
        boxes.append(salient_box)
    if not boxes:
        return 0.1

    risky = 0
    for box in boxes:
        left = box["x"]
        top = box["y"]
        right = box["x"] + box["w"]
        bottom = box["y"] + box["h"]
        if left < margin_x or top < margin_y or right > width - margin_x or bottom > height - margin_y:
            risky += 1
    return risky / max(len(boxes), 1)
