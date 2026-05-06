# Review Data Examples

This page explains:

- what data goes into the review system
- what data comes out of it
- how that output affects future generation
- how to think about the system in a data-science way without overcomplicating it

This is intentionally about the current system and the next conservative step, not a theoretical future ML system.

For the explicit implementation policy that maps these signals into generator effects, see [review_scoring_contract.md](/Users/rami/Documents/insta_autolayout/docs/review_scoring_contract.md).

## Core Idea

The current review system has three layers:

1. Raw review events
2. Derived review summaries / overrides
3. Generator score changes

That means:

```text
click feedback in review UI
-> saved JSONL event
-> derived manual-overrides.generated.json
-> candidate/source score nudges in the next run
```

## Raw Data In

The app currently records structured review events.

### Example 1: Whole video / concept feedback

```json
{
  "schema_version": 2,
  "reviewer_id": "rami",
  "project_id": "trybe",
  "batch_id": "trybe-branded-generic-find-people-sports",
  "concept_id": "video_01",
  "variant_id": "auto_soundtrack",
  "target": {
    "type": "concept"
  },
  "status": "needs_edit",
  "rating": -1,
  "reason_tags": ["weak_hook", "bad_music_fit"],
  "note": "The opener feels too soft and the soundtrack starts flat.",
  "generation_context": {
    "style": "clean_product_demo",
    "strategy": "people_motion",
    "score_total": 0.78
  }
}
```

What this means:

- It applies to the whole edit, not one source file or one trim.
- It is valuable for analysis and summaries.
- It should not yet automatically rewrite planner behavior in a strong way.

### Example 2: Source-level feedback

```json
{
  "schema_version": 2,
  "reviewer_id": "max",
  "project_id": "trybe",
  "batch_id": "trybe-branded-generic-find-people-sports",
  "concept_id": "video_01",
  "variant_id": "auto_soundtrack",
  "target": {
    "type": "source_file",
    "source_file": "/Users/max/OneDrive/Trybe/Batch Video/source/football_group.mov"
  },
  "status": "reject",
  "rating": -2,
  "reason_tags": ["off_brand", "too_shaky"],
  "note": "Feels chaotic and not like the right visual tone.",
  "generation_context": {
    "style": "clean_product_demo",
    "strategy": "people_motion"
  }
}
```

What this means:

- The underlying file is being judged.
- Future candidates from this file should usually be penalized.

### Example 3: Exact clip feedback

```json
{
  "schema_version": 2,
  "reviewer_id": "rami",
  "project_id": "trybe",
  "batch_id": "trybe-branded-generic-find-people-sports",
  "concept_id": "video_01",
  "variant_id": "auto_soundtrack",
  "target": {
    "type": "clip",
    "source_file": "/Users/rami/OneDrive/Trybe/Batch Video/source/football_group.mov",
    "candidate_id": "football_group_vid_03",
    "clip_token": "football_group.mov@4.2-5.1",
    "timeline_start": 2.1,
    "timeline_end": 3.0,
    "source_start": 4.2,
    "source_end": 5.1
  },
  "status": "needs_edit",
  "rating": -1,
  "reason_tags": ["bad_trim", "starts_too_late"],
  "note": "The action has already started before the cut comes in.",
  "generation_context": {
    "style": "fast_punchy",
    "strategy": "people_motion",
    "score_total": 0.84,
    "crop_strategy": "smart_crop"
  }
}
```

What this means:

- The source file itself might still be good.
- The exact trim is the problem.
- This should mainly affect that clip token or nearby trim logic later, not suppress the entire source.

## Derived Data Out

The current system converts those review events into a generated file:

```text
review_state/derived/manual-overrides.generated.json
```

### Example derived output

```json
{
  "prefer_files": [
    "/Users/rami/OneDrive/Trybe/Batch Video/source/friendly_board_game.mov"
  ],
  "avoid_files": [
    "/Users/max/OneDrive/Trybe/Batch Video/source/football_group.mov"
  ],
  "clip_ratings": {
    "football_group.mov@4.2-5.1": -1,
    "friendly_board_game.mov@8.0-9.0": 1
  }
}
```

What this means:

- `prefer_files`: all candidates from this source get a mild boost
- `avoid_files`: all candidates from this source get a stronger penalty
- `clip_ratings`: exact clip tokens get a direct numeric nudge

## How The Current Generator Uses It

Today the generator applies these conservative score shifts:

- preferred source file: `+0.12`
- avoided source file: `-0.18`
- clip rating: `rating * 0.08`

So if a candidate originally had `score_total = 0.71`:

- preferred source -> `0.83`
- avoided source -> `0.53`
- clip rating `-2` -> `0.55`
- clip rating `+1` -> `0.79`

The important thing is that these are nudges, not hard rules.

## Why Partial Review Is Not A Problem

If you review only 40 to 60 percent of a batch, the correct interpretation is:

- reviewed items receive evidence
- unreviewed items remain unknown
- unknown is not the same as neutral
- neutral is not the same as good

So the system should not assume:

- "not reviewed" means bad
- "not reviewed" means fine
- one clip problem means whole source is bad

Instead:

- clip events update clip-level evidence
- source events update source-level evidence
- concept events are stored for later summary and interpretation

## The Statistical Hierarchy

This is a useful way to think about the data.

### Nominal variables

Pure categories. No natural ordering.

- `target.type`
- `reason_tags`
- `strategy`
- `crop_strategy`
- `reviewer_id`
- `source_file`
- `candidate_id`

Use nominal variables for:

- routing logic
- grouping
- frequency analysis
- agreement analysis

### Ordinal variables

Ordered, but not necessarily evenly spaced.

- `rating` from `-2` to `2`
- `status` if treated cautiously

Use ordinal variables for:

- gentle monotonic influence
- ranking adjustments

Do not assume:

- the distance from `shortlist` to `approved` equals the distance from `needs_edit` to `reject`

### Ratio / count variables

True numeric quantities.

- event count
- number of reviewers agreeing
- clip durations
- source start / end times
- timeline positions
- score totals

Use these for:

- confidence
- coverage later if needed
- trend analysis

## How To Think Like A Data Scientist Here

The best mental model is not:

"How do we build a smart AI from all this?"

The best mental model is:

"What is the unit of analysis, what is the signal, and what is the safe effect size?"

### 1. Define the unit of analysis

In this system there are multiple units:

- concept
- source file
- clip token

That matters because each unit should influence a different part of the generator.

### 2. Separate signal from confidence

A review event has:

- direction: positive or negative
- confidence: how much we should trust it

Example:

- one clip marked `bad_trim` is a real signal
- but it is not enough evidence to suppress the whole source file

### 3. Keep scope local before making it global

This is the biggest discipline.

- bad clip != bad source
- bad source != bad concept strategy
- bad concept != bad clip

Always apply the smallest safe scope first.

### 4. Prefer interpretable transformations

For this project, the right first transformations are:

- source prefer / avoid
- clip rating boosts / penalties
- summary counts by reviewer / status / target type

These are explainable and easy to debug.

### 5. Delay complex inference until the data proves it

Do not jump straight into:

- strategy weights
- pacing model shifts
- hook prediction changes
- learned multi-feature scoring

until the simpler evidence path works reliably.

## Recommended First Interpretation Contract

This is the safest first model.

| Input event | Derived effect | Scope |
|---|---|---|
| `source_file` + positive status/tags | add source preference | source |
| `source_file` + negative status/tags | add source penalty | source |
| `clip` + positive trim/action tags | boost clip token | exact clip |
| `clip` + negative trim/crop/boundary tags | penalize clip token | exact clip |
| `concept` feedback | summary only for now | no automatic planner change yet |
| `brand_card` feedback | summary only for now | no automatic planner change yet |

## What Should Happen Next

The next safe product step is:

1. auto-apply source and clip derived feedback in app generation
2. log exactly which derived file was used
3. keep concept and brand-card feedback summary-only for now

That gives a real learning loop without pretending sparse feedback is stronger than it is.

## Short Summary

If you want to think like a data scientist here, ask:

1. What entity is being judged?
2. Is this nominal, ordinal, or numeric?
3. What is the smallest safe effect scope?
4. How strong should the effect be?
5. What would count as overfitting this signal?

That is exactly the right mindset for this phase of the project.
