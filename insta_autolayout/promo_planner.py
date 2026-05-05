from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass
from pathlib import Path

from .promo_models import TimelineItem, VideoConcept


@dataclass(frozen=True, slots=True)
class StrategyProfile:
    name: str
    label: str
    people_bias: float
    motion_bias: float
    photo_bias: float
    scenery_bias: float
    opener_energy_bonus: float


@dataclass(frozen=True, slots=True)
class BatchTuning:
    planned_count: int
    density_scale: float
    min_clip_floor: int
    max_clip_cap: int
    same_video_source_cap: int
    same_video_stem_gap: int
    anchor_budget: int
    relaxed_anchor_budget: int
    exact_candidate_cap: int
    video_source_cap: int
    image_source_cap: int
    video_stem_cap: int
    image_stem_cap: int
    exhaustion_duration_ratio: float
    exhaustion_min_clip_ratio: float
    allow_full_fallback: bool
    mode_label: str


FAST_PUNCHY_STRATEGIES: tuple[StrategyProfile, ...] = (
    StrategyProfile("people_motion", "people heavy with motion spikes", 0.20, 0.18, -0.04, -0.06, 0.12),
    StrategyProfile("nightlife_energy", "nightlife leaning energy cut", 0.16, 0.22, -0.02, -0.10, 0.15),
    StrategyProfile("travel_context", "travel context with punchy humans", 0.08, 0.10, 0.02, 0.10, 0.06),
    StrategyProfile("photo_assist", "photo assisted tempo build", 0.12, 0.08, 0.12, 0.02, 0.04),
    StrategyProfile("broad_mix", "broadest appeal fast cut", 0.12, 0.12, 0.00, 0.00, 0.08),
)

FAST_PUNCHY_VARIATION_ROUNDS: tuple[tuple[str, str, float, float, float, float, float], ...] = (
    ("core", "core pass", 0.00, 0.00, 0.00, 0.00, 0.00),
    ("video_bias", "video-forward pass", 0.02, 0.05, -0.05, -0.02, 0.02),
    ("photo_reset", "photo reset pass", -0.01, -0.02, 0.08, 0.03, -0.01),
    ("scenery_reset", "scenery reset pass", -0.04, -0.01, 0.00, 0.08, 0.00),
)


class PromoPlanner:
    def build_concepts(
        self,
        candidates,
        count: int,
        duration_min: float,
        duration_max: float,
        seed: str,
        style: str = "fast_punchy",
        punchiness: str = "fast",
        diversity_strength: float = 1.0,
    ) -> list[VideoConcept]:
        strategies = _strategy_sequence(count)
        tuning = _build_batch_tuning(candidates, count, style, punchiness)
        usage_counts: dict[str, int] = {}
        source_counts: dict[str, int] = {}
        source_stem_counts: dict[str, int] = {}
        opener_counts: dict[str, int] = {}
        concepts: list[VideoConcept] = []

        for index, profile in enumerate(strategies, start=1):
            rng = random.Random(_stable_seed(seed, f"{profile.name}:{index}"))
            target_duration = round(rng.uniform(duration_min, duration_max), 2)
            timeline = self._assemble_timeline(
                candidates=candidates,
                profile=profile,
                target_duration=target_duration,
                style=style,
                punchiness=punchiness,
                diversity_strength=max(0.0, diversity_strength),
                rng=rng,
                usage_counts=usage_counts,
                source_counts=source_counts,
                source_stem_counts=source_stem_counts,
                opener_counts=opener_counts,
                tuning=tuning,
            )
            candidate_ids = [item.candidate_id for item in timeline]
            source_files = [item.source_file for item in timeline]
            for candidate_id in candidate_ids:
                usage_counts[candidate_id] = usage_counts.get(candidate_id, 0) + 1
            for source_file in source_files:
                source_counts[source_file] = source_counts.get(source_file, 0) + 1
                stem = Path(source_file).stem.lower()
                source_stem_counts[stem] = source_stem_counts.get(stem, 0) + 1
            if timeline:
                opener_counts[timeline[0].candidate_id] = opener_counts.get(timeline[0].candidate_id, 0) + 1

            concepts.append(
                VideoConcept(
                    concept_id=f"video_{index:02d}",
                    style=style,
                    strategy=profile.name,
                    target_duration=target_duration,
                    why_this_version=f"Built as a {profile.label} pass with stronger penalty on already-used sources.",
                    diversity_notes=[
                        f"Target duration {target_duration:.1f}s",
                        "Favors different openers and different source files than earlier outputs",
                        f"Biases: people {profile.people_bias:+.2f}, motion {profile.motion_bias:+.2f}, photo {profile.photo_bias:+.2f}, scenery {profile.scenery_bias:+.2f}",
                        f"Punchiness: {punchiness}",
                        f"Diversity strength: {diversity_strength:.2f}",
                        _diversity_gate_note(diversity_strength),
                        _density_note(tuning),
                        _retirement_note(tuning),
                    ],
                    timeline=timeline,
                    used_candidate_ids=candidate_ids,
                    used_source_files=source_files,
                )
            )

        return concepts

    def _assemble_timeline(
        self,
        candidates,
        profile: StrategyProfile,
        target_duration: float,
        style: str,
        punchiness: str,
        diversity_strength: float,
        rng: random.Random,
        usage_counts: dict[str, int],
        source_counts: dict[str, int],
        source_stem_counts: dict[str, int],
        opener_counts: dict[str, int],
        tuning: BatchTuning,
    ) -> list[TimelineItem]:
        if not candidates:
            return []

        picked = []
        total_duration = 0.0
        desired_video_ratio = _desired_video_ratio(style, punchiness)
        min_clip_count = _min_clip_count(style, punchiness, target_duration, tuning)
        max_clip_count = _max_clip_count(style, punchiness, tuning)

        opener = self._choose_opener(
            candidates=candidates,
            profile=profile,
            usage_counts=usage_counts,
            source_counts=source_counts,
            source_stem_counts=source_stem_counts,
            opener_counts=opener_counts,
            diversity_strength=diversity_strength,
            punchiness=punchiness,
            tuning=tuning,
        )
        picked.append(opener)
        total_duration += opener.base_duration

        while (total_duration < target_duration or len(picked) < min_clip_count) and len(picked) < max_clip_count:
            next_candidate = self._choose_next(
                candidates=candidates,
                picked=picked,
                profile=profile,
                usage_counts=usage_counts,
                source_counts=source_counts,
                source_stem_counts=source_stem_counts,
                opener_counts=opener_counts,
                desired_video_ratio=desired_video_ratio,
                punchiness=punchiness,
                diversity_strength=diversity_strength,
                rng=rng,
                target_duration=target_duration,
                total_duration=total_duration,
                min_clip_count=min_clip_count,
                tuning=tuning,
            )
            if next_candidate is None:
                break
            picked.append(next_candidate)
            total_duration += next_candidate.base_duration

        return self._to_timeline_items(picked)

    def _choose_opener(
        self,
        candidates,
        profile: StrategyProfile,
        usage_counts: dict[str, int],
        source_counts: dict[str, int],
        source_stem_counts: dict[str, int],
        opener_counts: dict[str, int],
        diversity_strength: float,
        punchiness: str,
        tuning: BatchTuning,
    ):
        ranked = sorted(
            self._opener_pool(candidates),
            key=lambda candidate: self._candidate_value(
                candidate,
                profile,
                usage_counts,
                source_counts,
                source_stem_counts,
                opener_counts,
                True,
                diversity_strength=diversity_strength,
            ),
            reverse=True,
        )
        for strict in (True, False):
            for candidate in ranked:
                if strict and _blocked_by_batch_reuse(candidate, usage_counts, source_counts, source_stem_counts, diversity_strength, punchiness, tuning):
                    continue
                if not strict and _blocked_by_batch_reuse(candidate, usage_counts, source_counts, source_stem_counts, diversity_strength, punchiness, tuning, strict=False):
                    continue
                return candidate
        if tuning.allow_full_fallback:
            return ranked[0]
        return min(
            ranked,
            key=lambda candidate: (
                usage_counts.get(candidate.candidate_id, 0),
                source_counts.get(candidate.source_file, 0),
                opener_counts.get(candidate.candidate_id, 0),
                -candidate.score_total,
            ),
        )

    def _choose_next(
        self,
        candidates,
        picked,
        profile: StrategyProfile,
        usage_counts: dict[str, int],
        source_counts: dict[str, int],
        source_stem_counts: dict[str, int],
        opener_counts: dict[str, int],
        desired_video_ratio: float,
        punchiness: str,
        diversity_strength: float,
        rng: random.Random,
        target_duration: float,
        total_duration: float,
        min_clip_count: int,
        tuning: BatchTuning,
    ):
        video_count = sum(item.source_type == "video" for item in picked)
        current_video_ratio = video_count / max(len(picked), 1)
        in_final_stretch = total_duration >= (target_duration * 0.78)

        ranked = sorted(
            candidates,
            key=lambda candidate: self._candidate_value(
                candidate,
                profile,
                usage_counts,
                source_counts,
                source_stem_counts,
                opener_counts,
                False,
                picked=picked,
                diversity_strength=diversity_strength,
                current_video_ratio=current_video_ratio,
                desired_video_ratio=desired_video_ratio,
                in_final_stretch=in_final_stretch,
            ),
            reverse=True,
        )

        picked_candidate_ids = {item.candidate_id for item in picked}
        picked_files = [item.source_file for item in picked]
        for candidate in ranked:
            if _candidate_blocked(
                candidate,
                picked,
                picked_candidate_ids,
                picked_files,
                usage_counts,
                source_counts,
                source_stem_counts,
                diversity_strength,
                punchiness,
                in_final_stretch,
                tuning,
                strict=True,
            ):
                continue
            return candidate
        if _should_stop_for_exhaustion(picked, total_duration, target_duration, min_clip_count, tuning):
            return None
        for candidate in ranked:
            if _candidate_blocked(
                candidate,
                picked,
                picked_candidate_ids,
                picked_files,
                usage_counts,
                source_counts,
                source_stem_counts,
                diversity_strength,
                punchiness,
                in_final_stretch,
                tuning,
                strict=False,
            ):
                continue
            return candidate
        if not tuning.allow_full_fallback:
            return None
        for candidate in ranked:
            if candidate.candidate_id in picked_candidate_ids:
                continue
            if picked and candidate.source_file == picked[-1].source_file:
                continue
            if _same_source_stem(candidate.source_file, picked_files) and not _can_reuse_same_stem(candidate, picked, punchiness, tuning):
                continue
            if len([item for item in picked if item.source_file == candidate.source_file]) >= _same_file_limit(candidate, punchiness, tuning):
                continue
            if in_final_stretch and not self._allowed_in_final_stretch(candidate):
                continue
            return candidate
        return None

    def _candidate_value(
        self,
        candidate,
        profile: StrategyProfile,
        usage_counts: dict[str, int],
        source_counts: dict[str, int],
        source_stem_counts: dict[str, int],
        opener_counts: dict[str, int],
        opener: bool,
        picked=None,
        diversity_strength: float = 1.0,
        current_video_ratio: float = 0.0,
        desired_video_ratio: float = 0.55,
        in_final_stretch: bool = False,
    ) -> float:
        value = candidate.score_total
        value += profile.people_bias if "people" in candidate.tags else profile.scenery_bias
        value += profile.motion_bias * candidate.motion_energy
        value += profile.photo_bias if candidate.source_type == "image" else 0.08
        if opener:
            value += profile.opener_energy_bonus
            if candidate.source_type == "video":
                value += 0.06
            if candidate.face_count:
                value += 0.05
            value += 0.10 * candidate.boundary_confidence
            value -= opener_counts.get(candidate.candidate_id, 0) * 0.15
        value -= usage_counts.get(candidate.candidate_id, 0) * 0.45 * diversity_strength
        value -= source_counts.get(candidate.source_file, 0) * (0.34 if candidate.source_type == "image" else 0.18) * diversity_strength
        value -= source_stem_counts.get(Path(candidate.source_file).stem.lower(), 0) * (0.22 if candidate.source_type == "image" else 0.10) * diversity_strength

        if picked:
            last = picked[-1]
            if last.source_type == candidate.source_type:
                value -= 0.04
            if last.source_file == candidate.source_file:
                value -= 0.30
            if last.visual_role == candidate.visual_role:
                value -= 0.07
            if set(last.tags) & set(candidate.tags):
                value -= 0.02
            if _same_source_stem(candidate.source_file, [item.source_file for item in picked]):
                value -= 0.28
            if candidate.source_type == "video" and current_video_ratio < desired_video_ratio:
                value += 0.08
            if candidate.source_type == "image" and current_video_ratio > desired_video_ratio:
                value += 0.04
            if in_final_stretch:
                value += 0.06 * candidate.boundary_confidence
                if candidate.score_total < 0.72:
                    value -= 0.18
        return value

    def _opener_pool(self, candidates):
        pool = [
            candidate
            for candidate in candidates
            if candidate.score_total >= 0.70 and candidate.boundary_confidence >= 0.60 and candidate.source_type == "video"
        ]
        if pool:
            return pool
        pool = [candidate for candidate in candidates if candidate.score_total >= 0.68 and candidate.boundary_confidence >= 0.55]
        return pool or candidates

    def _allowed_in_final_stretch(self, candidate) -> bool:
        return candidate.score_total >= 0.72 and candidate.boundary_confidence >= 0.58

    def _to_timeline_items(self, picked) -> list[TimelineItem]:
        cursor = 0.0
        items: list[TimelineItem] = []
        for index, candidate in enumerate(picked):
            motion_treatment = "zoom_pulse" if candidate.source_type == "image" else ("speed_ramp" if candidate.playback_rate > 1.2 else "trimmed_clip")
            why = f"{candidate.why_candidate}; selected for role {candidate.visual_role}"
            duration = candidate.base_duration
            item = TimelineItem(
                candidate_id=candidate.candidate_id,
                source_file=candidate.source_file,
                source_type=candidate.source_type,
                source_start=candidate.source_start,
                source_end=candidate.source_end,
                timeline_start=round(cursor, 4),
                timeline_end=round(cursor + duration, 4),
                playback_rate=candidate.playback_rate,
                duration=duration,
                transition_in="none" if index == 0 else _fast_transition(index),
                transition_out=_fast_transition(index + 1),
                crop_strategy=candidate.crop_strategy,
                motion_treatment=motion_treatment,
                score_total=candidate.score_total,
                why_selected=why,
                score_breakdown=candidate.score_breakdown,
            )
            items.append(item)
            cursor += duration
        return items


def _stable_seed(*parts: str) -> int:
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()
    return int(digest[:12], 16)


def _strategy_sequence(count: int) -> list[StrategyProfile]:
    profiles: list[StrategyProfile] = []
    for index in range(max(0, count)):
        base = FAST_PUNCHY_STRATEGIES[index % len(FAST_PUNCHY_STRATEGIES)]
        round_index = index // len(FAST_PUNCHY_STRATEGIES)
        round_name, round_label, people_delta, motion_delta, photo_delta, scenery_delta, opener_delta = FAST_PUNCHY_VARIATION_ROUNDS[
            round_index % len(FAST_PUNCHY_VARIATION_ROUNDS)
        ]
        if round_index == 0:
            profiles.append(base)
            continue
        profiles.append(
            StrategyProfile(
                name=f"{base.name}_{round_name}",
                label=f"{base.label} {round_label}",
                people_bias=base.people_bias + people_delta,
                motion_bias=base.motion_bias + motion_delta,
                photo_bias=base.photo_bias + photo_delta,
                scenery_bias=base.scenery_bias + scenery_delta,
                opener_energy_bonus=base.opener_energy_bonus + opener_delta,
            )
        )
    return profiles


def _desired_video_ratio(style: str, punchiness: str) -> float:
    if style != "fast_punchy":
        return 0.55
    if punchiness == "hyper":
        return 0.74
    if punchiness == "normal":
        return 0.58
    return 0.66


def _min_clip_count(style: str, punchiness: str, target_duration: float, tuning: BatchTuning) -> int:
    if style != "fast_punchy":
        return 0
    if punchiness == "hyper":
        base = max(28, min(46, int(target_duration / 0.38)))
        return max(tuning.min_clip_floor, min(tuning.max_clip_cap, int(round(base * tuning.density_scale))))
    if punchiness == "normal":
        base = max(14, min(24, int(target_duration / 0.68)))
        return max(tuning.min_clip_floor, min(tuning.max_clip_cap, int(round(base * tuning.density_scale))))
    base = max(20, min(36, int(target_duration / 0.50)))
    return max(tuning.min_clip_floor, min(tuning.max_clip_cap, int(round(base * tuning.density_scale))))


def _max_clip_count(style: str, punchiness: str, tuning: BatchTuning) -> int:
    if style != "fast_punchy":
        return min(18, tuning.max_clip_cap)
    if punchiness == "hyper":
        return min(52, tuning.max_clip_cap)
    if punchiness == "normal":
        return min(28, tuning.max_clip_cap)
    return min(40, tuning.max_clip_cap)


def _same_file_limit(candidate, punchiness: str, tuning: BatchTuning) -> int:
    if candidate.source_type == "video":
        return tuning.same_video_source_cap
    return 1


def _can_reuse_same_stem(candidate, picked, punchiness: str, tuning: BatchTuning) -> bool:
    if candidate.source_type != "video" or punchiness not in {"fast", "hyper"}:
        return False
    same_stem = [item for item in picked if Path(item.source_file).stem.lower() == Path(candidate.source_file).stem.lower()]
    if not same_stem:
        return False
    if len(same_stem) >= _same_file_limit(candidate, punchiness, tuning):
        return False
    last_same_index = max(index for index, item in enumerate(picked) if Path(item.source_file).stem.lower() == Path(candidate.source_file).stem.lower())
    return len(picked) - last_same_index >= tuning.same_video_stem_gap


def _same_source_stem(source_file: str, picked_files: list[str]) -> bool:
    stem = Path(source_file).stem.lower()
    return any(Path(path).stem.lower() == stem for path in picked_files)


def _blocked_by_batch_reuse(candidate, usage_counts: dict[str, int], source_counts: dict[str, int], source_stem_counts: dict[str, int], diversity_strength: float, punchiness: str, tuning: BatchTuning, strict: bool = True) -> bool:
    """Hard gate for max-variety batches.

    Scores alone were not enough for hyper edits because 30+ clips per video
    made strong assets repeatedly win. This gate first tries unused candidates
    and, at higher diversity, unused stills/scenes before falling back.
    """
    if diversity_strength < 1.75:
        return False
    exact_cap = tuning.exact_candidate_cap if strict else tuning.exact_candidate_cap + 1
    if usage_counts.get(candidate.candidate_id, 0) >= exact_cap:
        return True

    stem = Path(candidate.source_file).stem.lower()
    if candidate.source_type == "image":
        source_cap = tuning.image_source_cap if strict else tuning.image_source_cap + 1
        stem_cap = tuning.image_stem_cap if strict else tuning.image_stem_cap + 1
        return source_counts.get(candidate.source_file, 0) >= source_cap or source_stem_counts.get(stem, 0) >= stem_cap

    source_cap = tuning.video_source_cap if strict else tuning.video_source_cap + 1
    stem_cap = tuning.video_stem_cap if strict else tuning.video_stem_cap + 1
    return source_counts.get(candidate.source_file, 0) >= source_cap or source_stem_counts.get(stem, 0) >= stem_cap


def _allowed_for_current_pass(candidate, strict: bool, diversity_strength: float) -> bool:
    if not strict or diversity_strength < 1.75:
        return candidate.score_total >= 0.72 and candidate.boundary_confidence >= 0.58
    # In max-variety mode, a decent unused clip is preferable to repeating
    # the same high-scoring source late in every video.
    return candidate.score_total >= 0.58 and candidate.boundary_confidence >= 0.45


def _diversity_gate_note(diversity_strength: float) -> str:
    if diversity_strength >= 2.35:
        return "Reuse gate: strict, avoids exact clips and heavily caps repeated source files."
    if diversity_strength >= 1.75:
        return "Reuse gate: moderate, avoids exact clips and repeated stills before fallback."
    return "Reuse gate: soft scoring only."


def _build_batch_tuning(candidates, count: int, style: str, punchiness: str) -> BatchTuning:
    if style != "fast_punchy":
        return BatchTuning(count, 1.0, 10, 18, 2, 8, 6, 8, 8, 18, 14, 18, 14, 0.84, 0.95, True, "standard")

    unique_candidates = len({candidate.candidate_id for candidate in candidates})
    unique_sources = len({candidate.source_file for candidate in candidates})
    unique_candidates_per_video = unique_candidates / max(count, 1)
    unique_sources_per_video = unique_sources / max(count, 1)

    if count >= 40:
        density_scale = 0.50 if unique_candidates_per_video < 4.0 else 0.56 if unique_candidates_per_video < 5.5 else 0.62
        return BatchTuning(
            planned_count=count,
            density_scale=density_scale,
            min_clip_floor=10 if punchiness == "hyper" else 9,
            max_clip_cap=26 if punchiness == "hyper" else 20,
            same_video_source_cap=1,
            same_video_stem_gap=12,
            anchor_budget=6,
            relaxed_anchor_budget=10,
            exact_candidate_cap=6,
            video_source_cap=12,
            image_source_cap=10,
            video_stem_cap=12,
            image_stem_cap=10,
            exhaustion_duration_ratio=0.62,
            exhaustion_min_clip_ratio=0.66,
            allow_full_fallback=False,
            mode_label="economy_hard",
        )
    if count >= 25:
        density_scale = 0.58 if unique_candidates_per_video < 6.5 else 0.68
        return BatchTuning(
            planned_count=count,
            density_scale=density_scale,
            min_clip_floor=14 if punchiness == "hyper" else 12,
            max_clip_cap=28 if punchiness == "hyper" else 22,
            same_video_source_cap=1,
            same_video_stem_gap=10,
            anchor_budget=6,
            relaxed_anchor_budget=10,
            exact_candidate_cap=6,
            video_source_cap=12,
            image_source_cap=10,
            video_stem_cap=12,
            image_stem_cap=10,
            exhaustion_duration_ratio=0.74,
            exhaustion_min_clip_ratio=0.82,
            allow_full_fallback=False,
            mode_label="economy",
        )
    if count >= 15 or unique_sources_per_video < 5.0:
        density_scale = 0.78
        return BatchTuning(
            planned_count=count,
            density_scale=density_scale,
            min_clip_floor=18 if punchiness == "hyper" else 14,
            max_clip_cap=34 if punchiness == "hyper" else 26,
            same_video_source_cap=2,
            same_video_stem_gap=8,
            anchor_budget=8,
            relaxed_anchor_budget=12,
            exact_candidate_cap=8,
            video_source_cap=16,
            image_source_cap=12,
            video_stem_cap=16,
            image_stem_cap=12,
            exhaustion_duration_ratio=0.78,
            exhaustion_min_clip_ratio=0.88,
            allow_full_fallback=True,
            mode_label="balanced",
        )
    return BatchTuning(
        planned_count=count,
        density_scale=1.0,
        min_clip_floor=20 if punchiness == "hyper" else 14,
        max_clip_cap=52 if punchiness == "hyper" else 40,
        same_video_source_cap=2,
        same_video_stem_gap=6,
        anchor_budget=12,
        relaxed_anchor_budget=16,
        exact_candidate_cap=12,
        video_source_cap=24,
        image_source_cap=16,
        video_stem_cap=24,
        image_stem_cap=16,
        exhaustion_duration_ratio=0.84,
        exhaustion_min_clip_ratio=0.95,
        allow_full_fallback=True,
        mode_label="full",
    )


def _density_note(tuning: BatchTuning) -> str:
    return (
        f"Density mode: {tuning.mode_label}, clip scale {tuning.density_scale:.2f}, "
        f"max clips {tuning.max_clip_cap}, anchor budget {tuning.anchor_budget}, "
        f"intra-video source cap {tuning.same_video_source_cap}"
    )


def _retirement_note(tuning: BatchTuning) -> str:
    return (
        f"Retirement caps: exact candidate {tuning.exact_candidate_cap}, "
        f"video source {tuning.video_source_cap}, image source {tuning.image_source_cap}"
    )


def _candidate_blocked(
    candidate,
    picked,
    picked_candidate_ids: set[str],
    picked_files: list[str],
    usage_counts: dict[str, int],
    source_counts: dict[str, int],
    source_stem_counts: dict[str, int],
    diversity_strength: float,
    punchiness: str,
    in_final_stretch: bool,
    tuning: BatchTuning,
    strict: bool,
) -> bool:
    if candidate.candidate_id in picked_candidate_ids:
        return True
    if _blocked_by_batch_reuse(candidate, usage_counts, source_counts, source_stem_counts, diversity_strength, punchiness, tuning, strict=strict):
        return True
    if _would_break_anchor_budget(candidate, picked, usage_counts, source_counts, tuning, strict):
        return True
    if picked and candidate.source_file == picked[-1].source_file:
        return True
    if _same_source_stem(candidate.source_file, picked_files) and not _can_reuse_same_stem(candidate, picked, punchiness, tuning):
        return True
    if len([item for item in picked if item.source_file == candidate.source_file]) >= _same_file_limit(candidate, punchiness, tuning):
        return True
    if in_final_stretch and not _allowed_for_current_pass(candidate, strict, diversity_strength):
        return True
    return False


def _would_break_anchor_budget(candidate, picked, usage_counts: dict[str, int], source_counts: dict[str, int], tuning: BatchTuning, strict: bool) -> bool:
    budget = tuning.anchor_budget if strict else tuning.relaxed_anchor_budget
    candidate_reused = usage_counts.get(candidate.candidate_id, 0) > 0 or source_counts.get(candidate.source_file, 0) > 0
    if not candidate_reused:
        return False
    reused_count = sum(1 for item in picked if usage_counts.get(item.candidate_id, 0) > 0 or source_counts.get(item.source_file, 0) > 0)
    return reused_count >= budget


def _should_stop_for_exhaustion(picked, total_duration: float, target_duration: float, min_clip_count: int, tuning: BatchTuning) -> bool:
    if not picked:
        return False
    enough_duration = total_duration >= target_duration * tuning.exhaustion_duration_ratio
    enough_clips = len(picked) >= max(1, int(min_clip_count * tuning.exhaustion_min_clip_ratio))
    return enough_duration and enough_clips


def _fast_transition(index: int) -> str:
    if index % 8 == 0:
        return "flash"
    if index % 5 == 0:
        return "dip_black"
    return "cut"
