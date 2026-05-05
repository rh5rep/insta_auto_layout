# Local-First Video Generation and Review System

## Purpose

This document is the living product and technical direction for evolving `insta_autolayout` from a batch HTML report generator into a local-first video generation and review app.

The immediate product is for Trybe video generation and review by Rami and Max. The architecture should also be reusable later for other projects, including Sunny Sips, where the same pattern can help generate, review, and improve social content without paid infrastructure.

The central question for every design decision is:

> How does this make it easier to generate better videos next time?

## Non-Technical Summary

The tool should feel like a simple local app, not a programming workflow.

The ideal user experience for Max is:

1. Run one command or open one app.
2. A browser page opens automatically.
3. Choose reviewer: Rami or Max.
4. Choose or confirm the media folder.
5. Click `Warm Cache`, `Generate Batch`, or `Open Latest Review`.
6. Review videos in a clean player.
7. Click simple feedback buttons.
8. The next generation run gets better because the system knows what was liked, rejected, or needs editing.

There should be no hosted backend, paid database, or shared server. The app runs locally on each person's computer and writes shared review files into OneDrive.

The review flow should capture useful feedback, not just opinions. It should distinguish:

- The whole video is good or bad.
- The source file is useful or not useful.
- A specific trim is good or bad.
- A cut starts too early, starts too late, ends too early, or ends too late.
- A clip has bad crop, bad pacing, weak hook, good action, or strong post potential.

That structure matters because different feedback should change different parts of the generator.

## Current System Audit

### What Already Works

The current project already has a strong local-first foundation:

- `insta_autolayout/__main__.py`
  - Runs generation from the command line.
  - Supports config files.
  - Supports input, output, archive output, cache dir, and manual overrides.
  - Supports `--warm-cache-only`.
  - Supports `--review-batch` to launch a local review server.

- `insta_autolayout/cache_store.py`
  - Stores local scan and candidate caches.
  - Uses signatures so caches invalidate when input media changes.
  - This is already the right model for local-only derived data.

- `insta_autolayout/scanner.py`
  - Scans media locally.
  - Extracts basic technical signals: dimensions, orientation, duration, sharpness, faces, saliency, edge risk.

- `insta_autolayout/promo_candidates.py`
  - Creates image and video candidates.
  - Scores candidates with explainable heuristics.
  - Applies feedback from manual overrides into candidate score deltas.

- `insta_autolayout/promo_planner.py`
  - Builds concepts using strategy profiles.
  - Uses diversity pressure to reduce repeated clips and sources.
  - Creates explainable timeline items with score breakdowns.

- `insta_autolayout/promo_exporter.py`
  - Exports `final.mp4`, `timeline.json`, `report.json`, `sequence.fcpxml`, `batch_manifest.json`, `overlap_report.json`, `index.html`, and `review.html`.
  - Already includes enough metadata to build a better review interface.

- `insta_autolayout/review_server.py`
  - Starts a local HTTP server.
  - Opens a browser.
  - Records feedback as JSONL.
  - Updates `manual-overrides.json`.

The most important existing loop is:

```text
review action -> feedback event -> manual overrides -> candidate score change -> better next generation
```

That loop should be preserved and made more structured.

### Current Gaps

The current system is useful but still too technical and too coarse for Rami and Max reviewing together.

Main gaps:

- The reviewer identity model is missing.
- Review state is not collaboration-safe enough for two people in OneDrive.
- The current `feedback_events.jsonl` is batch-local, not a clean shared canonical review state.
- `manual-overrides.json` is being used as both review output and generation input. It should become a generated tuning file, not the source of truth.
- The review UI is table-heavy and makes the reviewer map the video to rows manually.
- The current feedback buttons are useful but too limited:
  - concept feedback is mostly stored for later
  - source feedback is coarse
  - trim feedback is only good/bad
  - reason tags are missing
- The setup/generation flow still assumes a technical user.
- There is no single "open the app and use it" path.
- There is no project abstraction for Trybe now and Sunny Sips later.
- There is no clear audit trail connecting a generated batch to:
  - config used
  - reviewer feedback
  - derived overrides
  - next run behavior

## Product Direction

The next version should become a local app with two main modes.

### Mode 1: Setup / Run

Purpose: make generation usable without terminal knowledge.

Controls:

- Reviewer identity: `Rami` or `Max`.
- Project: `Trybe` now, `Sunny Sips` later.
- Input directory.
- Output directory.
- Archive directory.
- Shared review state directory.
- Cache directory.
- Music directory and soundtrack manifest.
- Config preset.
- Count, style, duration, scan depth, punchiness, diversity strength, audio variants.

Actions:

- Warm cache.
- Generate batch.
- Open latest review.
- Rebuild review summaries.
- Materialize generation feedback.

The first implementation can be simple HTML served by the local Python app. Native file pickers are nice later, but text fields with remembered paths are enough for the first version.

### Mode 2: Review

Purpose: make good judgment easy and turn that judgment into useful generation data.

The review screen should have:

- Large video player.
- Clip timeline directly under the player.
- Timeline segments proportional to duration.
- Active segment highlighted while the video plays.
- Clicking a segment seeks playback.
- Always-visible current clip/source panel.
- Concept status:
  - `unreviewed`
  - `shortlist`
  - `approved`
  - `needs_edit`
  - `reject`
- Separate feedback controls for:
  - whole video/concept
  - source file
  - exact trim/clip
- Reason tags.
- Optional note.
- Previous/next concept navigation.

The reviewer should never need to mentally connect the video to a table.

## Seamless Launch Strategy

The system should support three launch levels.

### Level 1: One CLI Command

This should be built first.

Example:

```bash
python -m insta_autolayout --app
```

Behavior:

- Starts local server on `127.0.0.1`.
- Opens browser automatically.
- Loads last-used settings.
- Lets Max use the app without remembering generation flags.

Pros:

- Fastest to build.
- Works with current Python project.
- Easy to debug.
- No extra packaging complexity.

Cons:

- Max still needs a terminal or a script/shortcut that runs it.

### Level 2: Double-Click Script

Create a macOS `.command` file or small shell script:

```text
Start Insta Autolayout.command
```

Behavior:

- Activates the virtual environment.
- Runs `python -m insta_autolayout --app`.
- Browser opens automatically.

Pros:

- Very friendly for Max.
- Low effort.
- No packaging dependency.
- Easy to update with Git.

Cons:

- macOS may require first-run permission.
- Still visibly opens Terminal.

### Level 3: Packaged Local Executable

Use a packager such as PyInstaller or Briefcase later.

Pros:

- Most polished.
- Max can open it like a normal app.
- Hides the technical setup.

Cons:

- Packaging video dependencies can be annoying.
- MoviePy, ffmpeg, PIL, and media codecs increase complexity.
- More work every time dependencies change.

Recommendation:

Build Level 1 now, add Level 2 immediately after, and defer Level 3 until the product flow is stable.

## SQLite Decision

SQLite is useful, but it should not be the canonical shared state in OneDrive.

### Why SQLite Is Tempting

Pros:

- Great for local querying.
- Fast indexes over batches, clips, sources, and review summaries.
- Easy to calculate dashboards.
- Useful for search, filtering, and "show me all rejected trims from this source".
- Mature and reliable on one machine.
- Excellent as a local cache.

### Why Not Use Shared SQLite In OneDrive As Truth

Cons:

- OneDrive sync is file-level, not database-transaction-aware collaboration.
- Two people writing to the same `.sqlite` file can create conflicts or lost updates.
- SQLite locks are local filesystem locks; they do not coordinate cleanly through cloud sync.
- Sync clients can duplicate files into conflict copies.
- Binary DB diffs are opaque in Git or OneDrive history.
- Recovery and merge behavior is worse than append-only text events.

For this project, shared SQLite would solve a convenience problem while creating a correctness problem.

### Recommended SQLite Role

Use SQLite later as a local derived index:

```text
OneDrive JSONL review events -> local SQLite index -> fast UI queries
```

SQLite should be rebuildable at any time from canonical files.

Safe uses:

- Local search index.
- Local dashboard cache.
- Local batch registry.
- Local clip/source lookup.
- Local analytics.

Unsafe use:

- Canonical shared review state written by both Rami and Max.

### Why Not Add SQLite Immediately

Do not start with SQLite because the first important problem is not query speed. The first important problem is a correct shared review model.

Build this first:

```text
structured JSONL events + summaries + derived overrides
```

Add SQLite when:

- summaries become slow to rebuild
- there are many batches
- UI needs fast filtering across many projects
- local analytics become painful with plain JSON files

This keeps the system simple while preserving the ability to add SQLite cleanly.

## Shared State Architecture

Canonical shared state should be file-based, append-only, and collaboration-safe.

Recommended OneDrive structure:

```text
OneDrive/Trybe/Batch Video Gen/
├── media/
├── batches/
├── review_state/
│   ├── project.json
│   ├── reviewers.json
│   ├── events/
│   │   └── <batch_id>/
│   │       ├── rami.jsonl
│   │       └── max.jsonl
│   ├── summaries/
│   │   └── <batch_id>.summary.json
│   └── derived/
│       ├── manual-overrides.generated.json
│       ├── source_scores.json
│       ├── clip_scores.json
│       ├── concept_scores.json
│       └── strategy_scores.json
└── archive/
```

Important collaboration rule:

```text
Rami writes only rami.jsonl.
Max writes only max.jsonl.
```

Summaries and derived files are rebuildable, so conflicts there are annoying but not dangerous.

## Project Model

The app should support projects because Trybe is the first use case, but Sunny Sips may become another use case.

Shared project config example:

```json
{
  "schema_version": 1,
  "projects": [
    {
      "project_id": "trybe",
      "display_name": "Trybe",
      "shared_root": "/Users/rami/Library/CloudStorage/OneDrive-DanmarksTekniskeUniversitet/Trybe/Batch Video Gen",
      "default_style": "fast_punchy",
      "brand": {
        "name": "Trybe"
      }
    },
    {
      "project_id": "sunny_sips",
      "display_name": "Sunny Sips",
      "shared_root": null,
      "default_style": "clean_product_demo",
      "brand": {
        "name": "Sunny Sips"
      }
    }
  ]
}
```

Trybe should be the first active project. Sunny Sips can be included as a future project placeholder without forcing implementation now.

## Reviewer Identity

Reviewer identity should be simple and explicit.

Shared reviewer metadata:

```json
{
  "schema_version": 1,
  "reviewers": [
    { "id": "rami", "display_name": "Rami" },
    { "id": "max", "display_name": "Max" }
  ]
}
```

Local user profile:

```json
{
  "reviewer_id": "rami",
  "display_name": "Rami"
}
```

Local profile path:

```text
~/.insta_autolayout/profile.json
```

The identity is for attribution, not security.

## Review Event Schema

Canonical review events should be append-only JSONL.

Example:

```json
{
  "schema_version": 2,
  "event_id": "20260429T142233Z-rami-8f31",
  "created_at": "2026-04-29T14:22:33Z",
  "reviewer_id": "rami",
  "project_id": "trybe",
  "batch_id": "2026-04-29_football_v1",
  "concept_id": "video_03",
  "variant_id": "auto_soundtrack",
  "target": {
    "type": "clip",
    "source_file": "/path/to/source.mov",
    "candidate_id": "IMG_1234_vid_03",
    "clip_token": "IMG_1234.mov@4.2-5.1",
    "timeline_start": 6.4,
    "timeline_end": 7.3,
    "source_start": 4.2,
    "source_end": 5.1
  },
  "status": "needs_edit",
  "rating": -1,
  "reason_tags": ["bad_trim", "starts_too_late"],
  "note": "The action already happened before this trim starts.",
  "generation_context": {
    "style": "fast_punchy",
    "strategy": "people_motion",
    "score_total": 0.82,
    "motion_energy": 0.47,
    "boundary_confidence": 0.61,
    "crop_strategy": "smart_crop"
  }
}
```

Target types:

- `concept`
- `source_file`
- `clip`
- `soundtrack`
- `text_overlay`
- `brand_card`

Statuses:

- `unreviewed`
- `shortlist`
- `approved`
- `needs_edit`
- `reject`

Recommended reason tags:

- `strong_hook`
- `weak_hook`
- `good_pacing`
- `bad_pacing`
- `repetitive`
- `postworthy`
- `off_brand`
- `bad_music_fit`
- `good_music_fit`
- `bad_trim`
- `good_trim`
- `starts_too_early`
- `starts_too_late`
- `ends_too_early`
- `ends_too_late`
- `bad_crop`
- `good_crop`
- `too_shaky`
- `too_slow`
- `good_action`
- `source_overused`
- `source_high_quality`

## Derived Generation Inputs

The generator should not read raw review events directly during planning. It should read derived feedback summaries.

Derived files:

```text
review_state/derived/source_scores.json
review_state/derived/clip_scores.json
review_state/derived/concept_scores.json
review_state/derived/strategy_scores.json
review_state/derived/manual-overrides.generated.json
```

For compatibility, the first implementation should generate:

```text
manual-overrides.generated.json
```

Then current code can keep using the existing override path.

Later, `promo_candidates.py` and `promo_planner.py` can consume richer derived files directly.

## How Feedback Improves Future Generation

Concept feedback improves:

- strategy weighting
- batch diversity settings
- soundtrack choice
- pacing defaults
- opener selection

Source feedback improves:

- candidate source ranking
- source reuse pressure
- source-level avoid/prefer decisions
- future batch diversity

Clip feedback improves:

- exact trim ranking
- trim boundary choice
- cut timing
- crop strategy choice
- playback speed decisions

Reason tags create actionable generator changes:

- `starts_too_late` means candidate windows should shift earlier.
- `starts_too_early` means windows should shift later.
- `ends_too_early` means extend or choose a later end.
- `ends_too_late` means shorten the trim.
- `bad_crop` means penalize that source/crop strategy pairing.
- `repetitive` means increase diversity pressure for similar source stems.
- `postworthy` means boost similar source, clip, and strategy patterns.

This is the main reason to structure review data carefully.

## Recommended Implementation Phases

### Phase 1: App Launch Foundation

Goal: make the tool easy for Max to open.

Build:

- Add `--app`.
- Start local server.
- Open browser automatically.
- Load/save local settings.
- Add reviewer picker: Rami or Max.
- Add project picker: Trybe now, Sunny Sips placeholder later.

Files:

- `insta_autolayout/__main__.py`
- new `insta_autolayout/app_server.py`
- new `insta_autolayout/local_settings.py`

Deliverable:

```bash
python -m insta_autolayout --app
```

### Phase 2: Shared-State Foundation

Goal: make review data collaboration-safe.

Build:

- Add review event schema.
- Add per-reviewer JSONL event files.
- Add shared review state root.
- Add summary rebuild.
- Add derived manual overrides.
- Keep old `/api/feedback` as compatibility shim.

Files:

- new `insta_autolayout/review_models.py`
- new `insta_autolayout/shared_state.py`
- new `insta_autolayout/feedback_adapter.py`
- update `insta_autolayout/review_server.py`

Deliverable:

```text
review_state/events/<batch_id>/rami.jsonl
review_state/events/<batch_id>/max.jsonl
review_state/summaries/<batch_id>.summary.json
review_state/derived/manual-overrides.generated.json
```

### Phase 3: Structured Review V2

Goal: make review judgment easy and useful.

Build:

- Large player.
- Synchronized timeline.
- Active clip highlighting.
- Click-to-seek timeline segments.
- Always-visible current clip panel.
- Concept/source/clip feedback controls.
- Status controls.
- Reason tags.
- Notes.

Files:

- `insta_autolayout/promo_exporter.py`
- `insta_autolayout/review_server.py`
- potentially new static asset helper module

Deliverable:

Review page that supports structured V2 events and is usable by Max without explanation.

### Phase 4: Setup / Run UI

Goal: make generation usable without terminal flags.

Build:

- Input/output/archive/shared-state directory settings.
- Config preset selector.
- Warm cache button.
- Generate batch button.
- Open latest review button.
- Recent batches list.
- Basic run logs.

Files:

- `insta_autolayout/app_server.py`
- `insta_autolayout/run_config.py`
- `insta_autolayout/__main__.py`

Deliverable:

Max can launch the app and generate/review without knowing the CLI flags.

### Phase 5: SQLite Local Index

Goal: speed and convenience after the file model is stable.

Build:

- Local SQLite database under `~/.insta_autolayout/index.sqlite`.
- Rebuild from OneDrive review events and batch manifests.
- Use for search, filtering, dashboards, and fast summaries.

Do not use SQLite as the canonical shared state.

Deliverable:

Fast local UI queries with fully rebuildable state.

### Phase 6: Generator Learning

Goal: make feedback directly improve generation quality.

Build:

- Source score integration.
- Clip score integration.
- Strategy score integration.
- Trim-boundary adjustment based on reason tags.
- Crop strategy penalty/boost based on review data.
- Diversity tuning based on repetition complaints.

Files:

- `insta_autolayout/promo_candidates.py`
- `insta_autolayout/promo_planner.py`
- `insta_autolayout/promo_audio.py`

Deliverable:

The generator produces better batches because prior reviews influence ranking, trimming, strategy, and diversity.

## What To Build Now

Build now:

- `--app` local launch.
- Reviewer identity: Rami and Max.
- Project identity: Trybe first, Sunny Sips placeholder.
- Shared review event schema.
- Per-reviewer JSONL files.
- Summary rebuild.
- Derived manual overrides.
- Review V2 player and synchronized timeline.

Defer:

- Packaged executable.
- SQLite index.
- Advanced analytics.
- Learned ranking model.
- Native desktop app.

## Product Principle

Do not add review controls just because they are possible.

Every review action should answer at least one of these:

- Should we post this video?
- Should we use this source again?
- Should we use this exact trim again?
- Should the generator change how it ranks, trims, crops, sequences, or diversifies clips?

If a button does not help produce better videos next time, it should not be in the first version.
