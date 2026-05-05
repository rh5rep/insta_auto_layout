from __future__ import annotations

import itertools
from dataclasses import dataclass

from PIL import Image

from .models import MediaAsset
from .rendering import protected_crop_score, render_image_to_canvas


@dataclass(frozen=True, slots=True)
class CollageTemplate:
    name: str
    boxes: tuple[tuple[float, float, float, float], ...]


TEMPLATES: dict[int, tuple[CollageTemplate, ...]] = {
    2: (
        CollageTemplate("2-up vertical split", ((0.0, 0.0, 0.5, 1.0), (0.5, 0.0, 0.5, 1.0))),
        CollageTemplate("2-up stacked", ((0.0, 0.0, 1.0, 0.5), (0.0, 0.5, 1.0, 0.5))),
    ),
    3: (
        CollageTemplate(
            "3-tile asymmetrical",
            ((0.0, 0.0, 0.62, 1.0), (0.62, 0.0, 0.38, 0.5), (0.62, 0.5, 0.38, 0.5)),
        ),
    ),
    4: (
        CollageTemplate(
            "4-grid",
            ((0.0, 0.0, 0.5, 0.5), (0.5, 0.0, 0.5, 0.5), (0.0, 0.5, 0.5, 0.5), (0.5, 0.5, 0.5, 0.5)),
        ),
    ),
}


def choose_best_collage_layout(
    assets: list[MediaAsset],
    canvas_size: tuple[int, int],
) -> tuple[CollageTemplate, list[MediaAsset], float]:
    templates = TEMPLATES[len(assets)]
    best: tuple[CollageTemplate, list[MediaAsset], float] | None = None

    for template in templates:
        for candidate_order in itertools.permutations(assets):
            score = _score_layout(candidate_order, template, canvas_size)
            if best is None or score > best[2]:
                best = (template, list(candidate_order), score)

    assert best is not None
    return best


def render_collage(
    assets: list[MediaAsset],
    asset_loader,
    canvas_size: tuple[int, int],
) -> tuple[Image.Image, str, float]:
    template, ordered_assets, score = choose_best_collage_layout(assets, canvas_size)
    canvas = Image.new("RGB", canvas_size, color=(245, 243, 239))
    canvas_width, canvas_height = canvas_size

    for asset, box in zip(ordered_assets, template.boxes, strict=True):
        left = int(round(box[0] * canvas_width))
        top = int(round(box[1] * canvas_height))
        width = int(round(box[2] * canvas_width))
        height = int(round(box[3] * canvas_height))
        image = asset_loader(asset)
        rendered = render_image_to_canvas(image, asset, (width, height), strategy="smart_crop")
        canvas.paste(rendered, (left, top))

    return canvas, template.name, score


def _score_layout(
    ordered_assets: tuple[MediaAsset, ...],
    template: CollageTemplate,
    canvas_size: tuple[int, int],
) -> float:
    canvas_width, canvas_height = canvas_size
    fit_scores = []
    for asset, box in zip(ordered_assets, template.boxes, strict=True):
        width = int(round(box[2] * canvas_width))
        height = int(round(box[3] * canvas_height))
        fit_scores.append(protected_crop_score(asset, width, height))

    balance_bonus = 0.08 if _orientation_mix(ordered_assets) else 0.0
    return sum(fit_scores) / len(fit_scores) + balance_bonus


def _orientation_mix(assets: tuple[MediaAsset, ...]) -> bool:
    return len({asset.orientation for asset in assets}) > 1
