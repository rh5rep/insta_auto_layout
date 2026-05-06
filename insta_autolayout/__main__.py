from __future__ import annotations

import argparse
import json
import shutil
import warnings
from pathlib import Path
from typing import Any

from .cache_store import (
    compute_asset_state_signature,
    compute_input_signature,
    default_cache_dir,
    load_candidates,
    load_scan_outcome,
    save_candidates,
    save_scan_outcome,
    write_manifest,
)
from .promo_audio import DEFAULT_AUDIO_VARIANTS, PromoAudioPlanner
from .promo_candidates import PromoCandidateBuilder, apply_candidate_feedback
from .promo_exporter import PromoExporter
from .promo_models import PromoOutput
from .promo_planner import PromoPlanner
from .review_server import serve_review_batch
from .soundtrack_library import SoundtrackLibrary
from .overrides import load_manual_overrides
from .ranker import apply_asset_filters, mark_duplicates
from .run_config import config_value, load_run_config
from .scanner import MediaScanner


PROGRESS_PREFIX = "__PROGRESS__ "


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate short 9:16 promo-video batches from a local media library.")
    parser.add_argument("--config", default=None, help="JSON config file for repeatable batch settings")
    parser.add_argument("--input", default=None, help="Input album folder")
    parser.add_argument("--output", default=None, help="Output folder")
    parser.add_argument("--archive-output", default=None, help="Optional sync/archive folder to copy completed local batches into after export")
    parser.add_argument("--cache-dir", default=None, help="Optional persistent cache folder for scan and candidate analysis")
    parser.add_argument("--app", action="store_true", help="Launch the local setup/review app")
    parser.add_argument("--review-batch", default=None, help="Serve an exported batch for review and feedback capture")
    parser.add_argument("--shared-state", default=None, help="Optional OneDrive/shared review state root")
    parser.add_argument("--reviewer", choices=["rami", "max"], default=None, help="Reviewer identity for structured review events")
    parser.add_argument("--project", default=None, help="Project id for review events, e.g. trybe or sunny_sips")
    parser.add_argument("--count", type=int, default=None, help="Number of promo concepts to generate")
    parser.add_argument("--style", choices=["fast_punchy", "clean_product_demo", "founder_personal_brand"], default=None)
    parser.add_argument("--duration-min", type=float, default=None)
    parser.add_argument("--duration-max", type=float, default=None)
    parser.add_argument("--scan-depth", choices=["quick", "balanced", "deep"], default=None, help="Candidate extraction quality/speed tradeoff")
    parser.add_argument("--punchiness", choices=["normal", "fast", "hyper"], default=None, help="Cut density, BPM preference, and soundtrack start behavior")
    parser.add_argument("--min-bpm", type=int, default=None, help="Minimum preferred BPM for auto soundtrack selection")
    parser.add_argument("--diversity-strength", type=float, default=None, help="Reuse penalty multiplier across a batch")
    parser.add_argument("--audio-variants", default=None, help="Comma-separated variants: silent,auto,bpm120,bpm128,bpm140,bpm150,bpm160,generated")
    parser.add_argument("--music-dir", default=None, help="Directory of approved local soundtrack files for the auto variant")
    parser.add_argument("--music-manifest", default=None, help="JSON manifest describing soundtrack metadata and optional direct file URLs")
    parser.add_argument("--seed", default=None, help="Deterministic seed for repeatable batches")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--explain", action="store_true")
    parser.add_argument("--warm-cache-only", action="store_true", help="Populate scan/candidate cache, then exit without planning or export")
    parser.add_argument("--manual-overrides", default=None, help="Path to manual-overrides.json")
    return parser


def main() -> None:
    warnings.filterwarnings(
        "ignore",
        message=r"In file .* bytes wanted but 0 bytes read.*Using the last valid frame instead\.",
        category=UserWarning,
        module=r"moviepy\.video\.io\.ffmpeg_reader",
    )
    parser = build_parser()
    args = parser.parse_args()
    if args.app:
        from .app_server import serve_local_app

        serve_local_app()
        return
    if args.review_batch:
        serve_review_batch(
            Path(args.review_batch),
            shared_state_dir=Path(args.shared_state).expanduser().resolve() if args.shared_state else None,
            reviewer_id=args.reviewer,
            project_id=args.project or "trybe",
        )
        return
    config = load_run_config(args.config)

    input_arg = config_value(args.input, config.input)
    output_arg = config_value(args.output, config.output)
    archive_output_arg = config_value(args.archive_output, config.archive_output)
    cache_dir_arg = config_value(args.cache_dir, config.cache_dir)
    shared_state_arg = config_value(args.shared_state, getattr(config, "shared_state", None))
    if not input_arg or not output_arg:
        raise SystemExit("--input and --output are required unless supplied by --config")

    count = config_value(args.count, config.count, 5)
    style = config_value(args.style, config.style, "fast_punchy")
    duration_min = config_value(args.duration_min, config.duration_min, 12.0)
    duration_max = config_value(args.duration_max, config.duration_max, 20.0)
    scan_depth = config_value(args.scan_depth, config.scan_depth, "balanced")
    punchiness = config_value(args.punchiness, config.punchiness, "fast")
    min_bpm = config_value(args.min_bpm, config.min_bpm, 132)
    diversity_strength = config_value(args.diversity_strength, config.diversity_strength, 1.0)
    audio_variants = config_value(args.audio_variants, config.audio_variants, ",".join(DEFAULT_AUDIO_VARIANTS))
    music_dir = config_value(args.music_dir, config.music_dir)
    music_manifest = config_value(args.music_manifest, config.music_manifest)
    seed = config_value(args.seed, config.seed, "default")
    manual_overrides = config_value(args.manual_overrides, config.manual_overrides)
    text_overlays = config.text_overlays
    brand_cards = config.brand_cards

    input_dir = Path(input_arg).expanduser().resolve()
    output_dir = Path(output_arg).expanduser().resolve()
    archive_output_dir = Path(archive_output_arg).expanduser().resolve() if archive_output_arg else None
    cache_dir = Path(cache_dir_arg).expanduser().resolve() if cache_dir_arg else default_cache_dir(input_dir)
    shared_state_dir = Path(shared_state_arg).expanduser().resolve() if shared_state_arg else None
    if not input_dir.exists():
        raise SystemExit(f"Input folder does not exist: {input_dir}")

    _emit_progress("setup", "Preparing overrides", current=0, total=1, percent=2)
    overrides = load_manual_overrides(input_dir, manual_overrides, shared_state_dir=shared_state_dir)
    input_signature = compute_input_signature(input_dir)
    outcome = load_scan_outcome(cache_dir, input_signature)
    scan_cache_hit = outcome is not None
    if outcome is None:
        _emit_progress("scan", "Scanning media library", current=0, total=1, percent=8)
        scanner = MediaScanner()
        outcome = scanner.scan(input_dir)
        save_scan_outcome(cache_dir, input_signature, outcome)
    if not outcome.assets:
        raise SystemExit("No supported images or videos found in the input folder.")

    filtered_assets, ranking_exclusions = apply_asset_filters(outcome.assets, overrides)
    if not filtered_assets:
        raise SystemExit("All assets were excluded by manual overrides.")

    mark_duplicates(filtered_assets)
    asset_state_signature = compute_asset_state_signature(filtered_assets)
    candidates = load_candidates(cache_dir, asset_state_signature, style, scan_depth)
    candidate_cache_hit = candidates is not None
    if candidates is None:
        _emit_progress("candidates", "Building candidates", current=0, total=1, percent=20)
        candidates = PromoCandidateBuilder(scan_depth=scan_depth).build(filtered_assets, style)
        save_candidates(cache_dir, asset_state_signature, style, scan_depth, candidates)
    write_manifest(cache_dir, input_dir, input_signature, asset_state_signature, style, scan_depth)
    candidates = apply_candidate_feedback(candidates, overrides)
    if not candidates:
        raise SystemExit("No usable promo candidates were produced from the input media.")
    if overrides.get("derived_path"):
        print(f"learned feedback: {overrides['derived_path']}")
    if overrides.get("manual_path"):
        print(f"manual overrides: {overrides['manual_path']}")
    print(f"cache: {cache_dir}")
    print(f"  scan: {'hit' if scan_cache_hit else 'warmed'}")
    print(f"  candidates[{style}/{scan_depth}]: {'hit' if candidate_cache_hit else 'warmed'}")
    if args.warm_cache_only:
        _emit_progress("completed", "Cache warm complete", current=1, total=1, percent=100)
        print("cache warm complete; exiting before planning/export.")
        return

    _emit_progress("audio", "Preparing soundtrack selection", current=0, total=1, percent=30)
    soundtrack_library = SoundtrackLibrary.from_sources(
        search_dirs=_resolve_music_dirs(input_dir, music_dir),
        manifest_path=Path(music_manifest).expanduser().resolve() if music_manifest else _default_music_manifest(input_dir),
    )
    audio_notes: list[dict[str, str]] = []
    if "auto" in _parse_audio_variants(audio_variants) and not soundtrack_library.has_available_tracks():
        audio_notes.append({"file": str(input_dir), "reason": "auto_soundtrack_skipped:no_music_tracks_found"})

    batch_specs = _batch_specs_from_config(
        config.batches,
        {
            "output": str(output_dir),
            "archive_output": str(archive_output_dir) if archive_output_dir else None,
            "count": count,
            "style": style,
            "duration_min": duration_min,
            "duration_max": duration_max,
            "scan_depth": scan_depth,
            "punchiness": punchiness,
            "min_bpm": min_bpm,
            "audio_variants": audio_variants,
            "seed": seed,
            "diversity_strength": diversity_strength,
            "text_overlays": text_overlays,
            "brand_cards": brand_cards,
        },
    )
    for batch_index, spec in enumerate(batch_specs, start=1):
        batch_output_dir = Path(spec["output"]).expanduser().resolve()
        variant_names = _parse_audio_variants(str(spec["audio_variants"]))
        audio_planner = PromoAudioPlanner(
            soundtrack_library=soundtrack_library,
            punchiness=str(spec["punchiness"]),
            min_bpm=int(spec["min_bpm"]),
        )
        _emit_progress("planning", f"Planning concepts for batch {batch_index}/{len(batch_specs)}", current=0, total=1, percent=40)
        concepts = PromoPlanner().build_concepts(
            candidates=candidates,
            count=max(1, min(int(spec["count"]), 200)),
            duration_min=max(4.0, min(float(spec["duration_min"]), float(spec["duration_max"]))),
            duration_max=max(float(spec["duration_min"]), float(spec["duration_max"])),
            seed=str(spec["seed"]),
            style=str(spec["style"]),
            punchiness=str(spec["punchiness"]),
            diversity_strength=float(spec["diversity_strength"]),
        )
        outputs = []
        for concept in concepts:
            variants = audio_planner.build_variants(concept, variant_names)
            report = _build_report(
                concept,
                variants,
                outcome.exclusions + ranking_exclusions + audio_notes,
                spec.get("text_overlays"),
                spec.get("brand_cards"),
            )
            outputs.append(PromoOutput(concept=concept, variants=variants, report=report))

        _emit_progress("rendering", f"Rendering {len(outputs)} concepts", current=0, total=max(len(outputs), 1), percent=45)
        PromoExporter(audio_planner=audio_planner).export_batch(
            outputs,
            filtered_assets,
            batch_output_dir,
            dry_run=args.dry_run,
            progress_callback=_export_progress_emitter(len(outputs)),
        )
        _emit_progress("finalizing", "Writing review assets", current=0, total=1, percent=96)
        review_context = {
            "input_dir": str(input_dir),
            "manual_overrides_path": str(overrides.get("path")) if overrides.get("path") else None,
            "manual_overrides_paths": list(overrides.get("paths", [])),
            "derived_overrides_path": str(overrides.get("derived_path")) if overrides.get("derived_path") else None,
            "using_generated_feedback": bool(overrides.get("using_generated_feedback")),
        }
        (batch_output_dir / "review_context.json").write_text(json.dumps(review_context, indent=2), encoding="utf-8")
        if spec.get("archive_output"):
            archive_dir = _archive_destination(Path(str(spec["archive_output"])).expanduser().resolve(), batch_output_dir)
            _copy_tree(batch_output_dir, archive_dir)
            print(f"archived batch copy: {archive_dir}")
        if len(batch_specs) > 1:
            print(f"\nbatch {batch_index}/{len(batch_specs)}: {batch_output_dir}")
        _print_summary(outputs, outcome.exclusions + ranking_exclusions + audio_notes)
        if args.explain:
            _print_explain(outputs)
    _emit_progress("completed", "Batch generation complete", current=1, total=1, percent=100)


def _print_summary(outputs, exclusions) -> None:
    excluded = "\n".join(f"  - {item['file']}: {item['reason']}" for item in exclusions) or "  - none"
    print("format: promo_video_batch")
    print(f"generated concepts: {len(outputs)}")
    if outputs:
        print(f"variants per concept: {', '.join(variant.render_name for variant in outputs[0].variants)}")
    print("excluded files and reasons:")
    print(excluded)
    for output in outputs:
        variants = ", ".join(variant.render_name for variant in output.variants)
        print(f"  - {output.concept.concept_id}: {output.concept.strategy}, {len(output.concept.timeline)} clips, variants [{variants}]")


def _print_explain(outputs) -> None:
    for output in outputs:
        print(f"\n{output.concept.concept_id}: {output.concept.strategy}")
        print(f"  why: {output.concept.why_this_version}")
        for note in output.concept.diversity_notes:
            print(f"  note: {note}")
        print("  timeline:")
        for index, item in enumerate(output.concept.timeline, start=1):
            print(
                f"    {index}. {Path(item.source_file).name} "
                f"{item.source_start:.1f}-{item.source_end:.1f}s "
                f"dur={item.duration:.2f}s rate={item.playback_rate:.2f} "
                f"score={item.score_total:.2f}"
            )
        print("  variants:")
        for variant in output.variants:
            extra = f" track={variant.track_title}" if variant.track_title else ""
            print(f"    - {variant.render_name}: bpm={variant.bpm} audio={variant.has_audio}{extra}")


def _emit_progress(stage: str, label: str, *, current: int | None = None, total: int | None = None, percent: int | None = None) -> None:
    payload: dict[str, Any] = {"stage": stage, "label": label}
    if current is not None:
        payload["current"] = current
    if total is not None:
        payload["total"] = total
    if percent is not None:
        payload["percent"] = percent
    print(PROGRESS_PREFIX + json.dumps(payload, sort_keys=True), flush=True)


def _export_progress_emitter(total_outputs: int):
    safe_total = max(total_outputs, 1)

    def callback(payload: dict[str, Any]) -> None:
        current = int(payload.get("current") or 0)
        total = int(payload.get("total") or safe_total)
        base = 45
        span = 50
        percent = base + round((min(max(current, 0), total) / max(total, 1)) * span)
        _emit_progress(
            str(payload.get("stage") or "rendering"),
            str(payload.get("label") or "Rendering concepts"),
            current=current,
            total=total,
            percent=min(percent, 95),
        )

    return callback

def _parse_audio_variants(raw: str) -> list[str]:
    allowed = {"silent", "auto", "generated"}
    values = [item.strip() for item in raw.split(",") if item.strip()]
    result = [
        item
        for item in values
        if item in allowed or (item.startswith("bpm") and item.removeprefix("bpm").isdigit() and 80 <= int(item.removeprefix("bpm")) <= 220)
    ]
    return result or list(DEFAULT_AUDIO_VARIANTS)


def _resolve_music_dirs(input_dir: Path, raw_music_dir: str | None) -> list[Path]:
    if raw_music_dir:
        return [Path(raw_music_dir).expanduser().resolve()]
    return [
        (input_dir / "music").resolve(),
        (input_dir / "music_cache").resolve(),
        (Path.cwd() / "music").resolve(),
        (Path.cwd() / "music_cache").resolve(),
    ]


def _default_music_manifest(input_dir: Path) -> Path | None:
    candidates = [
        (input_dir / "soundtrack_manifest.json").resolve(),
        (Path.cwd() / "soundtrack_manifest.json").resolve(),
    ]
    for path in candidates:
        if path.exists():
            return path
    return None


def _build_report(concept, variants, exclusions, text_overlays=None, brand_cards=None) -> dict:
    report = {
        "concept_id": concept.concept_id,
        "style": concept.style,
        "strategy": concept.strategy,
        "why_this_version": concept.why_this_version,
        "diversity_notes": concept.diversity_notes,
        "timeline": [item.to_dict() for item in concept.timeline],
        "variants": [variant.to_dict() for variant in variants],
        "excluded_assets": exclusions,
    }
    if isinstance(text_overlays, dict):
        report["text_overlays"] = text_overlays
    if isinstance(brand_cards, dict):
        report["brand_cards"] = brand_cards
    return report


def _batch_specs_from_config(raw_batches: list[dict[str, Any]] | None, defaults: dict[str, Any]) -> list[dict[str, Any]]:
    if not raw_batches:
        return [defaults]
    specs: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_batches, start=1):
        if not isinstance(raw, dict):
            continue
        spec = dict(defaults)
        for key, value in raw.items():
            normalized = key.replace("-", "_")
            if normalized == "audio_variants" and isinstance(value, list):
                spec[normalized] = ",".join(str(item).strip() for item in value if str(item).strip())
            elif value is not None:
                spec[normalized] = value
        if "name" in raw and "output" not in raw:
            spec["output"] = str(Path(defaults["output"]) / str(raw["name"]))
        elif "output_suffix" in raw and "output" not in raw:
            spec["output"] = f'{defaults["output"]}_{raw["output_suffix"]}'
        elif len(raw_batches) > 1 and "output" not in raw:
            spec["output"] = str(Path(defaults["output"]) / f"batch_{index:02d}")
        specs.append(spec)
    return specs or [defaults]


def _archive_destination(archive_root: Path, batch_output_dir: Path) -> Path:
    return archive_root / batch_output_dir.name


def _copy_tree(source: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    for path in source.rglob("*"):
        relative = path.relative_to(source)
        target = destination / relative
        if path.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)


if __name__ == "__main__":
    main()
