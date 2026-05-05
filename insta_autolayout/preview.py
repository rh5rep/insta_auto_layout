from __future__ import annotations

import json
import math
from pathlib import Path

from PIL import Image, ImageDraw

from .models import MediaAsset, Plan
from .moviepy_compat import VideoFileClip
from .rendering import frame_to_pil, render_image_to_canvas


class PreviewBuilder:
    def build(self, plan: Plan, assets: list[MediaAsset], output_dir: Path, dry_run: bool = False) -> None:
        assets_by_path = {asset.source_path: asset for asset in assets}
        html_path = output_dir / "preview.html"
        html_path.write_text(self._html(plan), encoding="utf-8")
        if not dry_run:
            self._contact_sheet(plan, assets_by_path, output_dir, output_dir / "contact_sheet.jpg")

    def build_variant_set(self, plans: list[Plan], assets: list[MediaAsset], output_dir: Path, dry_run: bool = False) -> None:
        output_dir.mkdir(parents=True, exist_ok=True)
        for plan in plans:
            variant_dir = output_dir if plan.variant_rank == 1 else output_dir / "variants" / (plan.variant_key or f"variant_{plan.variant_rank}")
            self.build(plan, assets, variant_dir, dry_run=dry_run)
        (output_dir / "variants" / "index.html").parent.mkdir(parents=True, exist_ok=True)
        (output_dir / "variants" / "index.html").write_text(self._variant_index_html(plans), encoding="utf-8")

    def _contact_sheet(
        self,
        plan: Plan,
        assets_by_path: dict[str, MediaAsset],
        output_dir: Path,
        output_path: Path,
    ) -> None:
        thumb_size = _thumb_size(plan.output_canvas)
        columns = 3
        rows = max(1, math.ceil(len(plan.slides) / columns))
        gutter = 24
        header = 54
        canvas = Image.new(
            "RGB",
            (columns * thumb_size[0] + (columns + 1) * gutter, rows * thumb_size[1] + (rows + 1) * gutter + header),
            color=(248, 246, 241),
        )
        draw = ImageDraw.Draw(canvas)
        draw.text((gutter, 16), f"{plan.chosen_mode} preview", fill=(35, 35, 35))

        for idx, slide in enumerate(plan.slides):
            col = idx % columns
            row = idx // columns
            x = gutter + col * (thumb_size[0] + gutter)
            y = gutter + row * (thumb_size[1] + gutter) + header
            preview = self._slide_preview_image(slide, assets_by_path, output_dir, thumb_size)
            canvas.paste(preview, (x, y))
            draw.rectangle((x, y, x + thumb_size[0], y + thumb_size[1]), outline=(205, 205, 205), width=2)
            draw.text((x, y - 20), f"Slide {slide.index}", fill=(45, 45, 45))
        canvas.save(output_path, quality=92)

    def _slide_preview_image(
        self,
        slide,
        assets_by_path: dict[str, MediaAsset],
        output_dir: Path,
        size: tuple[int, int],
    ) -> Image.Image:
        if slide.kind == "collage":
            export_path = output_dir / slide.export_path
            if export_path.exists():
                with Image.open(export_path) as image:
                    return image.convert("RGB").resize(size, Image.Resampling.LANCZOS)
        source = slide.source_files[0]
        asset = assets_by_path[source]
        if asset.media_type == "image":
            with Image.open(asset.source_path) as image:
                return render_image_to_canvas(
                    image=image,
                    asset=asset,
                    canvas_size=size,
                    strategy="smart_crop" if slide.crop_strategy == "smart_crop_or_pad" else slide.crop_strategy,
                )
        if VideoFileClip is not None:
            with VideoFileClip(asset.source_path, audio=False) as clip:
                frame = clip.get_frame(min((clip.duration or 0) * 0.25, max((clip.duration or 0) - 0.1, 0)))
            pil = frame_to_pil(frame)
            return render_image_to_canvas(pil, asset, size, "smart_crop")

        fallback = Image.new("RGB", size, color=(15, 15, 15))
        draw = ImageDraw.Draw(fallback)
        draw.text((20, 20), asset.file_name, fill=(255, 255, 255))
        return fallback

    def _html(self, plan: Plan) -> str:
        plan_json = json.dumps(plan.to_dict())
        aspect_css = _aspect_css(plan.target_aspect_ratio)
        variant_index_path = "variants/index.html" if plan.variant_rank == 1 else "../index.html"
        slide_cards = []
        for slide in plan.slides:
            source_list = "".join(f"<li>{Path(path).name}</li>" for path in slide.source_files)
            is_video = slide.export_path.endswith(".mp4")
            preview_block = (
                f'<video controls muted preload="metadata" src="{slide.export_path}"></video>'
                if is_video
                else f'<img src="{slide.export_path}" alt="Slide {slide.index} preview" />'
            )
            review_badge = (
                f'<span class="badge review">Manual review: {slide.review_reason or "check crop"}</span>'
                if slide.needs_manual_review
                else ""
            )
            slide_cards.append(
                f"""
                <article class="card" draggable="true" data-index="{slide.index}">
                  <div class="media">{preview_block}</div>
                  <div class="meta">
                    <div class="topline">
                      <strong>Slide {slide.index}</strong>
                      <span class="badge">{slide.kind}</span>
                    </div>
                    {review_badge}
                    <p>{slide.why_chosen}</p>
                    <p><strong>Crop:</strong> {slide.crop_strategy}</p>
                    <p><strong>Sources</strong></p>
                    <ul>{source_list}</ul>
                  </div>
                </article>
                """
            )

        return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>insta_autolayout preview</title>
  <style>
    :root {{
      --bg: #f6f1e8;
      --paper: #fffdf9;
      --ink: #261f1a;
      --accent: #c26a3d;
      --line: #d8c8b8;
    }}
    body {{
      margin: 0;
      font-family: Georgia, "Times New Roman", serif;
      color: var(--ink);
      background:
        radial-gradient(circle at top left, rgba(194, 106, 61, 0.12), transparent 28rem),
        linear-gradient(180deg, #f9f4ec 0%, #f2eadf 100%);
    }}
    main {{
      max-width: 1180px;
      margin: 0 auto;
      padding: 32px 20px 56px;
    }}
    .header {{
      display: flex;
      gap: 16px;
      align-items: flex-end;
      justify-content: space-between;
      flex-wrap: wrap;
      margin-bottom: 24px;
    }}
    .actions {{
      display: flex;
      gap: 12px;
      flex-wrap: wrap;
    }}
    button {{
      border: 0;
      padding: 12px 16px;
      background: var(--ink);
      color: white;
      cursor: pointer;
      border-radius: 999px;
    }}
    .grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
      gap: 18px;
    }}
    .card {{
      background: var(--paper);
      border: 1px solid var(--line);
      border-radius: 24px;
      overflow: hidden;
      box-shadow: 0 18px 40px rgba(80, 55, 36, 0.08);
    }}
    .card.dragging {{
      opacity: 0.45;
    }}
    .media {{
      background: #e8decf;
      aspect-ratio: {aspect_css};
      display: grid;
      place-items: center;
    }}
    img, video {{
      width: 100%;
      height: 100%;
      object-fit: cover;
      display: block;
    }}
    .meta {{
      padding: 16px 18px 18px;
    }}
    .topline {{
      display: flex;
      justify-content: space-between;
      gap: 12px;
      align-items: center;
    }}
    .badge {{
      display: inline-block;
      padding: 5px 10px;
      border-radius: 999px;
      background: #f3e1d3;
      color: #7d3d1e;
      font-size: 12px;
    }}
    .review {{
      background: #f7d9d4;
      color: #8a2d1d;
      margin-top: 10px;
    }}
    ul {{
      padding-left: 18px;
    }}
  </style>
</head>
<body>
  <main>
    <section class="header">
      <div>
        <h1>insta_autolayout preview</h1>
        <p><strong>Variant:</strong> {plan.variant_label or "Primary"}</p>
        <p><strong>Format:</strong> {plan.chosen_mode} | <strong>Target:</strong> {plan.target_aspect_ratio}</p>
        <p>{plan.recommended_caption_stub}</p>
      </div>
      <div class="actions">
        <button id="download-plan">Download reordered plan.json</button>
        <button id="open-variants" type="button">Compare Variants</button>
      </div>
    </section>
    <section class="grid" id="slides">
      {''.join(slide_cards)}
    </section>
  </main>
  <script>
    const originalPlan = {plan_json};
    const grid = document.getElementById("slides");
    let dragging = null;

    grid.querySelectorAll(".card").forEach((card) => {{
      card.addEventListener("dragstart", () => {{
        dragging = card;
        card.classList.add("dragging");
      }});
      card.addEventListener("dragend", () => {{
        card.classList.remove("dragging");
        dragging = null;
      }});
      card.addEventListener("dragover", (event) => {{
        event.preventDefault();
        if (!dragging || dragging === card) return;
        const rect = card.getBoundingClientRect();
        const before = event.clientY < rect.top + rect.height / 2;
        grid.insertBefore(dragging, before ? card : card.nextSibling);
      }});
    }});

    document.getElementById("download-plan").addEventListener("click", () => {{
      const cards = [...grid.querySelectorAll(".card")];
      const reorderedSlides = cards.map((card, index) => {{
        const originalIndex = Number(card.dataset.index);
        const slide = originalPlan.slides.find((item) => item.index === originalIndex);
        return {{ ...slide, index: index + 1 }};
      }});
      const payload = {{
        ...originalPlan,
        slides: reorderedSlides,
        review_slides: reorderedSlides.filter((slide) => slide.needs_manual_review).map((slide) => slide.index),
      }};
      const blob = new Blob([JSON.stringify(payload, null, 2)], {{ type: "application/json" }});
      const link = document.createElement("a");
      link.href = URL.createObjectURL(blob);
      link.download = "plan.reordered.json";
      link.click();
      URL.revokeObjectURL(link.href);
    }});

    document.getElementById("open-variants").addEventListener("click", () => {{
      window.location.href = "{variant_index_path}";
    }});
  </script>
</body>
</html>"""

    def _variant_index_html(self, plans: list[Plan]) -> str:
        cards = []
        for plan in plans:
            preview_path = "../preview.html" if plan.variant_rank == 1 else f"{plan.variant_key}/preview.html"
            contact_path = "../contact_sheet.jpg" if plan.variant_rank == 1 else f"{plan.variant_key}/contact_sheet.jpg"
            cards.append(
                f"""
                <article class="variant-card">
                  <a href="{preview_path}" class="thumb">
                    <img src="{contact_path}" alt="{plan.variant_label or 'Variant'} contact sheet" />
                  </a>
                  <div class="variant-meta">
                    <h2>{plan.variant_label or 'Variant'}</h2>
                    <p><strong>Rank:</strong> {plan.variant_rank} | <strong>Score:</strong> {plan.variant_score}</p>
                    <p><strong>Format:</strong> {plan.chosen_mode} | <strong>Target:</strong> {plan.target_aspect_ratio}</p>
                    <p><strong>Slides:</strong> {len(plan.slides)} | <strong>Review:</strong> {', '.join(map(str, plan.review_slides)) or 'none'}</p>
                    <p><a href="{preview_path}">Open variant preview</a></p>
                  </div>
                </article>
                """
            )

        return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>insta_autolayout variants</title>
  <style>
    body {{
      margin: 0;
      font-family: Georgia, "Times New Roman", serif;
      background: linear-gradient(180deg, #f9f4ec 0%, #f2eadf 100%);
      color: #261f1a;
    }}
    main {{
      max-width: 1200px;
      margin: 0 auto;
      padding: 32px 20px 56px;
    }}
    .grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
      gap: 18px;
    }}
    .variant-card {{
      background: #fffdf9;
      border: 1px solid #d8c8b8;
      border-radius: 24px;
      overflow: hidden;
      box-shadow: 0 18px 40px rgba(80, 55, 36, 0.08);
    }}
    .thumb img {{
      width: 100%;
      display: block;
      background: #e8decf;
    }}
    .variant-meta {{
      padding: 16px 18px 20px;
    }}
  </style>
</head>
<body>
  <main>
    <h1>Carousel Variants</h1>
    <p>Compare alternate orientations and pick the version that feels strongest before posting.</p>
    <section class="grid">
      {''.join(cards)}
    </section>
  </main>
</body>
</html>"""


def _thumb_size(output_canvas: tuple[int, int]) -> tuple[int, int]:
    width, height = output_canvas
    scale = 270 / width
    return (270, max(200, int(round(height * scale))))


def _aspect_css(target_aspect_ratio: str) -> str:
    left, right = target_aspect_ratio.split(":")
    return f"{left} / {right}"
