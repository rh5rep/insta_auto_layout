# insta_autolayout

`insta_autolayout` is now a local-first Python tool for turning a folder of mixed photos and videos into short `9:16` social promo-video batches.

The current MVP is optimized for fast, punchy lifestyle-brand edits with multiple timing/audio variants per concept.

## Features

- Scans mixed photo/video folders without touching originals
- Converts longer videos into multiple reusable candidate subclips
- Scores images and video windows for technical quality, vertical fit, motion, subject strength, and style fit
- Builds multiple distinct `9:16` promo concepts per run
- Exports multiple variants for each concept: `silent`, BPM-template, and optional auto-picked soundtrack variants
- Writes editable `timeline.json`, `report.json`, and `FCPXML`
- Writes batch-level `overlap_report.json` so repeated clips/scenes are measurable
- Renders final `mp4` outputs
- Can copy completed local batches into a sync folder after export
- Generates a simple HTML batch index
- Supports manual exclusions through `manual-overrides.json`
- Supports soundtrack auto-picking from a local `music/` folder, `music_cache/`, or a JSON manifest
- Supports `--dry-run` and `--explain`

## Install

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

`ffmpeg` should also be available on your system path for video export.

## Usage

```bash
python -m insta_autolayout --input ./album --output ./out
```

Local app launch:

```bash
python -m insta_autolayout --app
```

On macOS, you can also double-click [Start Insta Autolayout.command](/Users/rami/Documents/insta_autolayout/Start%20Insta%20Autolayout.command) to open the local app without typing CLI flags. The launcher prefers `./.venv/bin/python` when present and falls back to `python3` or `python`.

On Windows, you can also double-click [Start Insta Autolayout.bat](/Users/rami/Documents/insta_autolayout/Start%20Insta%20Autolayout.bat) to open the local app without typing CLI flags.

On Linux, use `python -m insta_autolayout --app` after installing the dependencies and ensuring `ffmpeg` is available on the system path.

Promo-batch usage:

```bash
python -m insta_autolayout \
  --input ./library \
  --output ./out \
  --archive-output ~/OneDrive/social-archive \
  --count 5 \
  --style fast_punchy \
  --duration-min 12 \
  --duration-max 20 \
  --scan-depth quick \
  --punchiness hyper \
  --min-bpm 140 \
  --audio-variants silent,bpm120,bpm128,auto \
  --explain
```

### CLI flags

- `--count 5`
- `--config ./promo_config.example.json`
- `--archive-output ~/OneDrive/social-archive`
- `--style fast_punchy|clean_product_demo|founder_personal_brand`
- `--duration-min 12`
- `--duration-max 20`
- `--scan-depth quick|balanced|deep`
- `--punchiness normal|fast|hyper`
- `--min-bpm 132`
- `--audio-variants silent,bpm120,bpm128,bpm140,bpm150,bpm160,auto,generated`
- `--music-dir ./music`
- `--music-manifest ./soundtrack_manifest.json`
- `--seed default`
- `--dry-run`
- `--explain`
- `--manual-overrides ./manual-overrides.json`

CLI flags override values from `--config`.

For multi-batch exploration, use:

```bash
python -m insta_autolayout --config ./promo_config_hyper_multibatch.json
```

That example generates four 10-video batches with increasingly strong reuse penalties. At `diversity_strength >= 1.75`, the planner first tries to avoid exact clip reuse and repeated stills before falling back. At `diversity_strength >= 2.35`, it also heavily caps repeated source videos for max-variety batches.

Important:

- Diversity resets between separate batches because each batch plans independently.
- If you want diversity pressure across all outputs, generate one large batch such as `--count 50` instead of five separate 10-video batches.
- `--archive-output` copies a finished local batch into the archive/sync folder after export; it does not render directly into the sync folder.

If `--manual-overrides` is omitted, the tool automatically looks for `manual-overrides.json` inside the input folder.

## Shared review state

To share review state between machines:

- Each person runs the app locally on their own machine.
- Each person points `Shared state directory` at their own synced copy of the same OneDrive folder.
- Rami should use reviewer id `rami`.
- Max should use reviewer id `max`.

The app writes canonical shared review events as:

```text
review_state/events/<batch_id>/rami.jsonl
review_state/events/<batch_id>/max.jsonl
```

It also rebuilds:

```text
review_state/summaries/<batch_id>.summary.json
review_state/derived/manual-overrides.generated.json
```

Important:

- Do not have both reviewers write to the same reviewer id.
- The JSONL sharing model is collaboration-safe because each reviewer appends only to their own file.
- When `Shared state directory` is set in the app, the next generated batch automatically uses `review_state/derived/manual-overrides.generated.json` if that file exists.
- For concrete examples of review events, derived outputs, and how they affect future generation, see [docs/review_data_examples.md](/Users/rami/Documents/insta_autolayout/docs/review_data_examples.md).
- For the explicit scoring/interpretation contract between review data and generator behavior, see [docs/review_scoring_contract.md](/Users/rami/Documents/insta_autolayout/docs/review_scoring_contract.md).

## Max Setup

For a first-time setup on Max's machine:

1. Clone the repo and install the Python dependencies.
2. Install `ffmpeg` and make sure it is on the system path.
3. Make sure OneDrive is syncing the shared Trybe folder locally on Max's machine.
4. Start the app with [Start Insta Autolayout.bat](/Users/rami/Documents/insta_autolayout/Start%20Insta%20Autolayout.bat) on Windows or `python -m insta_autolayout --app` on Linux.
5. In the app, set `Reviewer` to `Max`.
6. Point `Shared state directory` at Max's local synced copy of the shared OneDrive root.
7. Confirm the `Startup Checks` section shows no blocking errors before generating or reviewing.

The app now surfaces startup warnings for common setup mistakes such as missing `ffmpeg`, a missing shared-state path, or missing music/config files.

## Manual overrides

Example `manual-overrides.json`:

```json
{
  "exclude_files": ["IMG_1044.jpg", "clip_07.mov"],
  "prefer_files": ["IMG_1005.MOV"],
  "avoid_files": ["IMG_5793.jpeg"],
  "clip_ratings": {
    "IMG_1005.MOV@8.0-12.0": 2,
    "IMG_9023.MOV": -1
  },
  "pin_hero": "IMG_1042.jpg"
}
```

Rules:

- File names can be given as base names or relative paths under the album
- `exclude_files` removes files before candidate extraction
- `prefer_files` boosts every candidate from those files
- `avoid_files` downranks files without fully excluding them
- `clip_ratings` lets you score a whole file, a candidate id, or an approximate video time range from `-3` to `+3`
- `pin_hero` is not yet used by the promo planner, but remains reserved for later tuning

## Output structure

Example output:

```text
out/
├── batch_manifest.json
├── overlap_report.json
├── index.html
├── video_01/
│   ├── report.json
│   ├── timeline.json
│   ├── silent/
│   │   ├── final.mp4
│   │   ├── sequence.fcpxml
│   │   └── timeline.json
│   ├── auto_soundtrack/
│   │   ├── final.mp4
│   │   ├── audio.mp3
│   │   └── sequence.fcpxml
│   └── bpm128/
│       ├── final.mp4
│       └── sequence.fcpxml
└── video_02/
    └── ...
```

## How ranking works

The MVP deliberately stays heuristic and explainable:

- Long videos are split into reusable candidate windows
- Fast-punchy scoring favors motion, human presence, crop safety, and vertical fit
- Batch generation penalizes over-reuse so concepts differ from each other
- High-diversity batches use a hard first-pass reuse gate because scoring alone tends to repeat the strongest files in dense hyper edits
- `overlap_report.json` reports pairwise overlap by exact candidate, source file, and source stem so you can tell when two outputs are genuinely similar
- Large single batches keep diversity pressure active across the whole run; separate batches intentionally reset that pressure
- BPM-template variants retime the same concept against different rhythm grids
- Auto soundtrack variants select the closest approved track by BPM, energy, and strategy tags, then carry source/license metadata into the export
- `quick` scan depth is recommended for broad batch exploration; use `balanced` or `deep` when you want slower, cleaner trim-point refinement
- `hyper` punchiness targets denser cuts, allows separated repeated trims from strong videos, prefers faster/higher-energy tracks, and starts music past likely intros

## Development smoke test

```bash
python sample_test.py
```

## Notes

- The strongest path right now is `fast_punchy` `9:16` promo generation
- `auto` only works when the tool can find approved music in `music/`, `music_cache/`, or `soundtrack_manifest.json`
- `generated` remains available for synthetic rhythm testing, but it is not a production soundtrack
- See [soundtrack_suggestions.md](/Users/rami/Documents/insta_autolayout/soundtrack_suggestions.md) for a few manual-download candidate tracks to seed `music_cache/`
- External stock fallback is not implemented yet
