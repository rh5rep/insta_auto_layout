# Review Scoring Contract

This document defines the first conservative interpretation layer between:

- raw review events
- derived review signals
- generator behavior

It is intentionally narrow. The goal is not to use every possible signal. The goal is to use only the signals we can justify with sparse human review.

## Purpose

This contract answers:

- what kind of variable each feedback field is
- how it should be aggregated
- how confident we should be in it
- what derived output it should produce
- what part of generation it is allowed to influence

## Principles

1. Raw review events remain the source of truth.
2. Unknown is not negative.
3. Apply the smallest safe scope first.
4. Source and clip feedback are safe to auto-apply first.
5. Concept and brand-card feedback should remain summary-only until we prove a good interpretation path.

## Entity Hierarchy

| Entity | Scope | Safe first automatic effect |
|---|---|---|
| `clip` | exact trim | yes |
| `source_file` | all candidates from one media file | yes |
| `concept` | whole video | not yet |
| `brand_card` | intro/outro card | not yet |

## Variable Types

| Field | Statistical type | Use |
|---|---|---|
| `target.type` | nominal | routing only |
| `reason_tags` | nominal multi-label | evidence categories |
| `status` | ordinal-like categorical | structured interpretation, not raw arithmetic |
| `rating` | ordinal | small monotonic numeric signal |
| `reviewer_id` | nominal | agreement / disagreement |
| `source_file`, `candidate_id`, `clip_token` | nominal identifiers | grouping keys |
| `score_total`, `timeline_start`, `source_start`, `source_end` | ratio | context, later diagnostics |
| event counts | count | confidence |

## Current Raw Event To Derived Output Path

```text
raw event
-> entity-specific interpretation
-> derived source/clip score
-> manual-overrides.generated.json
-> candidate ranking adjustments
```

## Confidence Model

The first version should use low-complexity confidence, not learned confidence.

### Confidence components

| Component | Meaning | Suggested effect |
|---|---|---|
| event count | how many times this entity was reviewed | more events = more confidence |
| reviewer agreement | whether Rami and Max point in the same direction | agreement strengthens effect |
| recency | how old the signal is | older signals can decay later |
| scope fit | whether the feedback is local or global | local evidence stays local |

### First conservative confidence rules

| Condition | Confidence |
|---|---:|
| one event from one reviewer | low |
| repeated events from same reviewer across batches | medium |
| both reviewers agree | high |
| conflicting reviewers | reduce net effect |

For the first implementation, this can be represented as simple multipliers rather than a formal probability model.

## Status Interpretation

Do not map statuses directly as if they were evenly spaced numbers.

### Conceptual meaning

| Status | Desirability | Edit needed |
|---|---:|---:|
| `approved` | high positive | low |
| `shortlist` | moderate positive | low to medium |
| `needs_edit` | mixed | high |
| `reject` | negative | low salvageability |
| `unreviewed` | unknown | unknown |

### Practical rule

- `approved` and `shortlist` are positive but not identical
- `needs_edit` is not the same as reject
- `reject` is the strongest negative
- `unreviewed` creates no signal

## Scoring Contract Table

This is the actual contract to implement against.

| Event target | Input signal | Variable type | Aggregation rule | Confidence rule | Derived output | Generator impact |
|---|---|---|---|---|---|---|
| `source_file` | `approved`, `shortlist` | categorical | add positive source vote | one reviewer = low, both agree = high | source preference score | mild boost to all candidates from that source |
| `source_file` | `reject` | categorical | add strong negative source vote | one reviewer = medium, both agree = high | source penalty score | stronger penalty to all candidates from that source |
| `source_file` | positive tags like `source_high_quality`, `prefer_more_like_this`, `good_action` | nominal | add positive source vote | per-event confidence | source preference score | mild boost |
| `source_file` | negative tags like `off_brand`, `source_overused`, `avoid_more_like_this`, `too_shaky`, `too_slow` | nominal | add negative source vote | per-event confidence | source penalty score | penalty or higher reuse suppression |
| `source_file` | `rating > 0` | ordinal | add small positive source vote | low unless repeated | source preference score | mild boost |
| `source_file` | `rating < 0` | ordinal | add small negative source vote | low unless repeated | source penalty score | mild penalty |
| `clip` | positive trim tags like `good_trim`, `good_crop`, `good_action` | nominal | add positive clip vote to exact clip token | local evidence only | clip score | boost exact clip token |
| `clip` | negative trim tags like `bad_trim`, `starts_too_early`, `starts_too_late`, `ends_too_early`, `ends_too_late`, `bad_crop` | nominal | add negative clip vote to exact clip token | local evidence only | clip score | penalize exact clip token |
| `clip` | `approved`, `shortlist` | categorical | add positive clip vote | local evidence only | clip score | boost exact clip token |
| `clip` | `needs_edit` | categorical | add mild negative clip vote | local evidence only | clip score | penalize exact clip token, but do not suppress source |
| `clip` | `reject` | categorical | add strong negative clip vote | local evidence only | clip score | stronger penalty to exact clip token |
| `clip` | ordinal `rating` | ordinal | accumulate bounded numeric clip delta | exact token only | clip rating | direct score nudge |
| `concept` | `approved`, `shortlist`, `needs_edit`, `reject` | categorical | aggregate only | low confidence by default | concept summary | no automatic planner effect yet |
| `concept` | tags like `weak_hook`, `good_pacing`, `bad_music_fit`, `postworthy` | nominal | aggregate only | low confidence by default | strategy/pacing summary | no automatic planner effect yet |
| `brand_card` | readability / offer / brand fit tags | nominal | aggregate only | low confidence by default | brand-card summary | no automatic generation effect yet |

## First Derived Files

The interpretation layer should aim to produce these files:

| File | Purpose |
|---|---|
| `manual-overrides.generated.json` | immediate compatibility layer for generation |
| `source_scores.json` | explainable source-level evidence |
| `clip_scores.json` | explainable clip-token evidence |
| `concept_scores.json` | summary-only for now |
| `strategy_scores.json` | summary-only for now |

## Example Aggregation

### Raw events

```text
1. Rami marks source A as reject + off_brand
2. Max marks source A as reject + too_shaky
3. Rami marks clip A@4.2-5.1 as bad_trim
4. Max marks clip A@4.2-5.1 as starts_too_late
5. Rami marks concept video_01 as needs_edit + weak_hook
```

### Derived interpretation

```text
source A:
- two independent negative source signals
- both reviewers agree on negative direction
- strong source penalty

clip A@4.2-5.1:
- two independent negative clip signals
- both local to exact clip token
- strong exact clip penalty

concept video_01:
- useful summary signal
- do not yet auto-change planner strategy
```

### Resulting output shape

```json
{
  "prefer_files": [],
  "avoid_files": ["source_A.mov"],
  "clip_ratings": {
    "source_A.mov@4.2-5.1": -2
  }
}
```

## What This Contract Explicitly Does Not Do Yet

The first implementation should not:

- infer watch coverage
- infer attention
- infer latent pacing models from sparse concept tags
- adjust planner strategies automatically from one or two concept reviews
- suppress an entire source because one exact clip had a trim problem

Those are all valid later ideas, but not part of the first conservative loop.

## Implementation Sequence

| Step | Action |
|---|---|
| 1 | auto-apply source and clip derived feedback in generation |
| 2 | log which derived feedback file was used |
| 3 | expose source/clip derived evidence in generated metadata |
| 4 | keep concept and brand-card feedback summary-only |
| 5 | inspect a few review/generation cycles |
| 6 | only then consider concept-to-planner effects |

## Data-Science Mindset

The right questions are:

1. What entity is being judged?
2. What type of variable is this?
3. What is the smallest safe effect?
4. What would count as overfitting?
5. What evidence would justify a stronger effect later?

That is the working mindset this project should use before adding more automatic learning behavior.
