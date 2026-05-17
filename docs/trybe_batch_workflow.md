# Trybe Batch Workflow

This is the default operating loop for generating Trybe videos, reviewing them, and using that review to improve the next batch.

## Goal

Use small, repeatable batches to learn quickly:

1. Explore a content bucket.
2. Review aggressively.
3. Regenerate with the learned feedback.
4. Narrow toward publishable event invites or branded promos.

Do not generate a huge pile before reviewing. The system is designed to compound from review state.

## Standard Content Buckets

Keep runs separate by asset set. Recommended buckets:

- `trybe_generic_sports`
- `trybe_event_football`
- `trybe_event_climbing`
- `trybe_event_board_games`

If a bucket gets too broad, split it again. The point is to let feedback stay specific.

## Presets To Keep Stable

Use one stable config per workflow shape:

- Explore: [promo_config_trybe_generic_explore_onedrive.json](/Users/rami/Documents/insta_autolayout/promo_config_trybe_generic_explore_onedrive.json)
- Generic sports invite: [promo_config_trybe_generic_sports_invite_onedrive.json](/Users/rami/Documents/insta_autolayout/promo_config_trybe_generic_sports_invite_onedrive.json)
- Football invite: [promo_config_trybe_event_football_onedrive.json](/Users/rami/Documents/insta_autolayout/promo_config_trybe_event_football_onedrive.json)
- Climbing invite: [promo_config_trybe_event_climbing_onedrive.json](/Users/rami/Documents/insta_autolayout/promo_config_trybe_event_climbing_onedrive.json)
- Board games invite: [promo_config_trybe_event_board_games_onedrive.json](/Users/rami/Documents/insta_autolayout/promo_config_trybe_event_board_games_onedrive.json)

Keep seeds and copy inside those files versioned deliberately. Avoid changing five things at once.

## First-Time Setup

Launch the app:

```bash
python -m insta_autolayout --app
```

Set these once:

- `Project`: `trybe`
- `Reviewer`: `rami` or `max`
- input path for the current bucket
- output path for the current run family
- cache directory
- music directory
- soundtrack manifest
- shared state or Supabase credentials

Then run `Warm Cache` before your first real batch on a new asset set.

## Weekly Operating Loop

### 1. Explore

Start with an exploration batch, not a polished invite.

Use the explore config:

```bash
python -m insta_autolayout --config ./promo_config_trybe_generic_explore_onedrive.json
```

That config is intentionally shaped as three sub-batches:

- `balanced`
- `stronger_diversity`
- `explore_more`

This gives you variety across seeds and reuse pressure without making the run too large to review.

Target volume:

- 18 to 24 concepts total per exploration cycle
- 1 audio variant per concept during exploration

### 2. Review

Open the latest review in the app and review all concepts before generating again.

Review decisions should be explicit:

- whole concept: `shortlist`, `approved`, `needs_edit`, `reject`
- source usefulness: whether the underlying asset should appear again
- trim usefulness: whether the exact in/out points worked
- reason tags: hook, crop, pacing, action, energy, clarity

Use review to answer concrete questions:

- Which sources consistently produce good openings?
- Which clips are overused?
- Which trims look promising but start or end in the wrong place?
- Which assets should be excluded entirely?

### 3. Regenerate

Only after review is complete, run the next batch.

The next run should reuse the same bucket and a related config, while changing only one of:

- seed
- diversity strength
- headline / copy for invite variants
- asset folder, if you intentionally narrowed the source set

Do not rewrite style, duration, and copy all at once or you lose the value of the review signal.

### 4. Narrow Into Publishable Outputs

Once the exploration batch reveals good source material, switch to the relevant invite preset:

- football -> [promo_config_trybe_event_football_onedrive.json](/Users/rami/Documents/insta_autolayout/promo_config_trybe_event_football_onedrive.json)
- climbing -> [promo_config_trybe_event_climbing_onedrive.json](/Users/rami/Documents/insta_autolayout/promo_config_trybe_event_climbing_onedrive.json)
- board games -> [promo_config_trybe_event_board_games_onedrive.json](/Users/rami/Documents/insta_autolayout/promo_config_trybe_event_board_games_onedrive.json)
- generic branded -> [promo_config_trybe_generic_sports_invite_onedrive.json](/Users/rami/Documents/insta_autolayout/promo_config_trybe_generic_sports_invite_onedrive.json)

These should stay smaller:

- 2 to 4 concepts per run
- branded cards enabled
- cleaner pacing
- copy tuned for the specific activity

## Review Standard

Treat review like labeling training data, not just taste.

What to mark:

- `approved`: good enough to publish or very near
- `shortlist`: strong concept, worth keeping in the next round
- `needs_edit`: right material, wrong trim/order/hook
- `reject`: not worth teaching the system to repeat

What to note:

- strong open
- weak open
- crop problem
- pacing drift
- strong motion
- good social energy
- weak payoff
- wrong source for this concept family

## Default Cadence

For one activity bucket:

1. warm cache once
2. run one exploration cycle
3. review everything
4. run one narrowed follow-up
5. review again
6. run one publish-focused invite batch

That is usually enough to tell whether the asset pool is viable.

## Practical Rules

- Keep one reviewer id per person. Do not share reviewer ids.
- Keep the same project id: `trybe`.
- Prefer one audio variant during exploration to reduce render cost.
- Use higher diversity before you add more total count.
- If the same 3 to 5 sources keep winning, split the asset folder and test a narrower set.
- If a concept is close but weak at the start, mark the trim issue instead of rejecting the source.
- If an asset is consistently bad, exclude it instead of hoping a future seed rescues it.

## Recommended Directory Pattern

Use stable output roots so review is easy to reopen:

```text
Trybe/Batch Video Gen/
  trybe-generic-explore/
  trybe-branded-generic-find-people-sports/
  trybe-branded-event-pickup-football/
  trybe-branded-event-climbing-night/
  trybe-branded-event-board-game-night/
```

Use stable cache roots too:

```text
scratch/
  trybe-explore-cache/
  trybe-invite-cache/
```

## Minimal Starting Point

If you want the default first move, do this:

1. launch the app
2. set `Project = trybe`
3. warm cache on the target asset folder
4. run [promo_config_trybe_generic_explore_onedrive.json](/Users/rami/Documents/insta_autolayout/promo_config_trybe_generic_explore_onedrive.json)
5. review the full batch
6. switch to the matching invite preset based on what actually worked
