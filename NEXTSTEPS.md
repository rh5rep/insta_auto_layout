# Next Steps

See `docs/local_first_review_system.md` for the living product and technical direction.

## Immediate Priorities

- Done: Add one-command local app launch: `python -m insta_autolayout --app`
- Done: Add reviewer identity: Rami and Max
- Done: Add project identity: Trybe first, Sunny Sips placeholder later
- Done: Add shared review state root for OneDrive
- Done: Store canonical review events as per-reviewer JSONL files
- Done: Generate review summaries and derived manual overrides
- Done: Replace table-heavy review UI with Structured Review V2:
  - large player
  - synchronized clip timeline
  - active clip/source panel
  - concept/source/clip feedback
  - statuses and reason tags
- Done: Keep the app focused on Trybe for now
- Done: Add path picker/open buttons for local file system paths
- Done: Add an in-app how-to drawer
- Done: Make warm-cache and generation jobs non-blocking with progress logs
- Done: Add Trybe generation presets for event invites and punchy exploration runs
- Done: Add setup navigation from batch and review pages
- Done: Fix batch card clip counts by reading `report.json`
- Done: Include intro/outro brand cards in the review timeline
- Done: Make review feedback tags target-aware for whole video, source, clip, and brand-card moments
- Done: Add bounded generation controls for real supported CLI settings
- Done: test the app with a real OneDrive Trybe batch end to end with Rami and Max reviewer settings
- Done: make a double-click launcher for Max

## Later

- Add local SQLite index for fast search and dashboards
- Feed richer review summaries into candidate scoring, trim selection, planner strategy weights, and diversity tuning
- Do we want to do this with audio/text scripts
- Max will have 2 cut-up videos
- I will try and get v0 of review platform
- Figure out simple storage/file transfer through programatic interfaces because of onedrive organizational issues 
