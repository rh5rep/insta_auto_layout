from __future__ import annotations

import math
from pathlib import Path

import numpy as np

from .promo_models import AudioVariant, TimelineItem
from .soundtrack_library import SoundtrackLibrary


DEFAULT_AUDIO_VARIANTS = ("silent", "bpm120", "bpm128")


class PromoAudioPlanner:
    def __init__(self, soundtrack_library: SoundtrackLibrary | None = None, punchiness: str = "fast", min_bpm: int = 132) -> None:
        self.soundtrack_library = soundtrack_library or SoundtrackLibrary()
        self._track_usage: dict[str, int] = {}
        self.punchiness = punchiness if punchiness in {"normal", "fast", "hyper"} else "fast"
        self.min_bpm = min_bpm

    def build_variants(self, concept, variant_names: list[str]) -> list[AudioVariant]:
        variants: list[AudioVariant] = []
        for name in variant_names:
            if name == "silent":
                timeline = _retime_timeline(concept.timeline, None)
                variants.append(
                    AudioVariant(
                        variant_id=f"{concept.concept_id}_silent",
                        audio_mode="silent",
                        bpm=None,
                        has_audio=False,
                        display_name="Silent",
                        render_name="silent",
                        timeline=timeline,
                        report_notes=["No audio attached. Timing uses the concept's natural fast-cut pacing."],
                    )
                )
            elif name.startswith("bpm"):
                bpm = int(name.removeprefix("bpm"))
                timeline = _retime_timeline(concept.timeline, bpm, self.punchiness, concept.target_duration)
                variants.append(
                    AudioVariant(
                        variant_id=f"{concept.concept_id}_{name}",
                        audio_mode="bpm_template",
                        bpm=bpm,
                        has_audio=False,
                        display_name=f"BPM Template {bpm}",
                        render_name=name,
                        timeline=timeline,
                        report_notes=[f"Shot lengths snapped to a {bpm} BPM rhythm grid without adding audio."],
                    )
                )
            elif name == "auto":
                variant = self._build_auto_variant(concept)
                if variant is not None:
                    variants.append(variant)
            elif name == "generated":
                bpm = _autopick_bpm(concept.timeline)
                timeline = _retime_timeline(concept.timeline, bpm, self.punchiness, concept.target_duration)
                variants.append(
                    AudioVariant(
                        variant_id=f"{concept.concept_id}_generated",
                        audio_mode="generated_sound",
                        bpm=bpm,
                        has_audio=True,
                        display_name=f"Generated Beat {bpm} BPM",
                        render_name="generated_sound",
                        timeline=timeline,
                        track_label=f"generated_pulse_{bpm}",
                        report_notes=[f"Attached a generated beat bed at {bpm} BPM for synthetic rhythm testing."],
                    )
                )
        return variants

    def prepare_audio_asset(self, variant: AudioVariant, output_path: Path, dry_run: bool = False) -> Path | None:
        if not variant.has_audio:
            return None
        if variant.audio_mode == "generated_sound":
            if variant.bpm is None:
                return None
            if not dry_run:
                _write_generated_track(output_path, variant.timeline[-1].timeline_end if variant.timeline else 0.0, variant.bpm)
            return output_path
        if variant.track_original_path or variant.track_download_url:
            if variant.track_original_path:
                source = Path(variant.track_original_path)
                if dry_run:
                    return output_path
                if not source.exists():
                    return None
                output_path.parent.mkdir(parents=True, exist_ok=True)
                import shutil

                shutil.copy2(source, output_path)
                return output_path
            if variant.track_download_url:
                if dry_run:
                    return output_path
                from urllib.request import urlretrieve

                output_path.parent.mkdir(parents=True, exist_ok=True)
                try:
                    urlretrieve(variant.track_download_url, output_path)
                    return output_path
                except Exception:
                    return None
        return None

    def _build_auto_variant(self, concept) -> AudioVariant | None:
        fallback_bpm = _autopick_bpm(concept.timeline)
        min_bpm = self.min_bpm if self.punchiness in {"fast", "hyper"} else None
        soundtrack, selection_reason = self.soundtrack_library.pick_for_concept(
            concept,
            self._track_usage,
            fallback_bpm,
            min_bpm=min_bpm,
            prefer_high_energy=self.punchiness == "hyper",
        )
        if soundtrack is None:
            return
        bpm = max(soundtrack.bpm or fallback_bpm, min_bpm or fallback_bpm)
        timeline = _retime_timeline(concept.timeline, bpm, self.punchiness, concept.target_duration)
        track_start_sec = 18.0 if self.punchiness == "hyper" else 8.0 if self.punchiness == "fast" else 0.0
        self._track_usage[soundtrack.track_id] = self._track_usage.get(soundtrack.track_id, 0) + 1
        notes = [f"Attached an auto-picked soundtrack at {bpm} BPM using {self.punchiness} punchiness."]
        if track_start_sec:
            notes.append(f"Starts the music {track_start_sec:.1f}s into the track to avoid slow intros.")
        if soundtrack.license_name:
            notes.append(f"License: {soundtrack.license_name}")
        if soundtrack.credit:
            notes.append(f"Credit: {soundtrack.credit}")
        return AudioVariant(
            variant_id=f"{concept.concept_id}_auto",
            audio_mode="auto_soundtrack",
            bpm=bpm,
            has_audio=True,
            display_name=f"Auto Soundtrack {bpm} BPM",
            render_name="auto_soundtrack",
            timeline=timeline,
            track_label=soundtrack.track_id,
            track_title=soundtrack.title,
            track_source=soundtrack.source,
            track_license=soundtrack.license_name,
            track_credit=soundtrack.credit,
            track_original_path=soundtrack.local_path,
            track_download_url=soundtrack.download_url,
            track_page_url=soundtrack.page_url,
            track_start_sec=track_start_sec,
            selection_reason=selection_reason,
            report_notes=notes,
        )


def _retime_timeline(items: list[TimelineItem], bpm: int | None, punchiness: str = "fast", target_duration: float | None = None) -> list[TimelineItem]:
    if not items:
        return []
    if bpm is None:
        cursor = 0.0
        retimed = []
        for index, item in enumerate(items):
            duration = item.duration
            transition = _transition_for(index)
            retimed.append(_replace_item(item, cursor, cursor + duration, duration, transition))
            cursor += duration - _overlap_seconds(transition)
        return retimed

    beat = 60.0 / bpm
    if punchiness == "hyper":
        pattern = [0.5, 0.5, 1, 0.5, 1, 0.5, 0.5, 1, 0.5, 1, 1.5, 0.5, 0.5, 1, 2]
        minimum = 0.24
    elif punchiness == "normal":
        pattern = [4, 4, 6, 4, 6, 8, 4, 6]
        minimum = 0.9
    else:
        pattern = [1, 1, 2, 1, 2, 1, 1, 2, 1, 2, 2, 1, 1, 2, 4]
        minimum = 0.36
    cursor = 0.0
    retimed = []
    base_durations = _beat_grid_durations(items, pattern, beat, minimum)
    durations = _stretch_to_target(base_durations, target_duration, minimum)
    for index, (item, duration) in enumerate(zip(items, durations, strict=False)):
        transition = _transition_for(index)
        retimed.append(_replace_item(item, cursor, cursor + duration, duration, transition))
        cursor += duration - _overlap_seconds(transition)
    return retimed


def _beat_grid_durations(items: list[TimelineItem], pattern: list[float], beat: float, minimum: float) -> list[float]:
    durations = []
    for index, item in enumerate(items):
        beats = pattern[index % len(pattern)]
        if item.source_type == "image" and beats > 2:
            beats = 2
        durations.append(max(minimum, beats * beat))
    return durations


def _stretch_to_target(durations: list[float], target_duration: float | None, minimum: float) -> list[float]:
    if not durations or target_duration is None:
        return durations
    current = sum(durations)
    if current <= 0:
        return durations
    # Leave a small buffer for transition overlaps applied during retiming.
    desired = target_duration * 1.04
    if current >= desired * 0.99:
        return durations
    stretch = desired / current
    # Cap stretch so hyper edits stay fast. If the candidate pool is too short,
    # we would rather stay a little under target than create slow holds.
    stretch = min(stretch, 1.85)
    return [max(minimum, duration * stretch) for duration in durations]


def _replace_item(item: TimelineItem, start: float, end: float, duration: float, transition: str) -> TimelineItem:
    return TimelineItem(
        candidate_id=item.candidate_id,
        source_file=item.source_file,
        source_type=item.source_type,
        source_start=item.source_start,
        source_end=item.source_end,
        timeline_start=round(start, 4),
        timeline_end=round(end, 4),
        playback_rate=item.playback_rate,
        duration=round(duration, 4),
        transition_in=transition if start > 0 else "none",
        transition_out=transition,
        crop_strategy=item.crop_strategy,
        motion_treatment=item.motion_treatment,
        score_total=item.score_total,
        why_selected=item.why_selected,
        score_breakdown=item.score_breakdown,
    )


def _transition_for(index: int) -> str:
    if index == 0:
        return "cut"
    if index % 5 == 0:
        return "crossfade"
    if index % 3 == 0:
        return "dip_black"
    return "cut"


def _overlap_seconds(transition: str) -> float:
    if transition == "crossfade":
        return 0.10
    if transition == "dip_black":
        return 0.04
    return 0.0


def _autopick_bpm(items: list[TimelineItem]) -> int:
    avg_score = sum(item.score_total for item in items) / max(len(items), 1)
    avg_duration = sum(item.duration for item in items) / max(len(items), 1)
    if avg_score > 0.78 and avg_duration < 1.4:
        return 150
    if avg_score > 0.70:
        return 140
    return 132


def _write_generated_track(path: Path, duration: float, bpm: int, sample_rate: int = 44100) -> None:
    duration = max(duration + 0.3, 1.0)
    samples = int(duration * sample_rate)
    timeline = np.arange(samples, dtype=np.float32) / sample_rate
    beat_sec = 60.0 / bpm

    signal = np.zeros(samples, dtype=np.float32)
    signal += _bass_pulse(timeline, beat_sec)
    signal += _kick_pattern(timeline, beat_sec)
    signal += _snare_pattern(timeline, beat_sec)
    signal += _hat_pattern(timeline, beat_sec)
    signal = signal / max(np.max(np.abs(signal)), 1e-6)
    stereo = np.column_stack([signal, signal])

    path.parent.mkdir(parents=True, exist_ok=True)
    _write_wav(path, stereo, sample_rate)


def _bass_pulse(timeline: np.ndarray, beat_sec: float) -> np.ndarray:
    out = np.zeros_like(timeline)
    for beat in np.arange(0, timeline[-1] + beat_sec, beat_sec * 2):
        env = np.exp(-18 * np.maximum(0.0, timeline - beat))
        out += 0.16 * np.sin(2 * math.pi * 55 * np.maximum(0.0, timeline - beat)) * env
    return out


def _kick_pattern(timeline: np.ndarray, beat_sec: float) -> np.ndarray:
    out = np.zeros_like(timeline)
    for beat in np.arange(0, timeline[-1] + beat_sec, beat_sec):
        local = np.maximum(0.0, timeline - beat)
        env = np.exp(-32 * local)
        freq = 110 - (65 * np.minimum(local / 0.12, 1.0))
        out += 0.55 * np.sin(2 * math.pi * freq * local) * env
    return out


def _snare_pattern(timeline: np.ndarray, beat_sec: float) -> np.ndarray:
    rng = np.random.default_rng(11)
    noise = rng.normal(0, 1, size=len(timeline)).astype(np.float32)
    out = np.zeros_like(timeline)
    for beat in np.arange(beat_sec, timeline[-1] + beat_sec, beat_sec * 2):
        local = np.maximum(0.0, timeline - beat)
        env = np.exp(-42 * local)
        out += 0.18 * noise * env
    return out


def _hat_pattern(timeline: np.ndarray, beat_sec: float) -> np.ndarray:
    rng = np.random.default_rng(7)
    noise = rng.normal(0, 1, size=len(timeline)).astype(np.float32)
    out = np.zeros_like(timeline)
    for beat in np.arange(0, timeline[-1] + beat_sec / 2, beat_sec / 2):
        local = np.maximum(0.0, timeline - beat)
        env = np.exp(-80 * local)
        out += 0.04 * noise * env
    return out


def _write_wav(path: Path, stereo: np.ndarray, sample_rate: int) -> None:
    import wave

    pcm = np.clip(stereo * 32767, -32768, 32767).astype(np.int16)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(2)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm.tobytes())
