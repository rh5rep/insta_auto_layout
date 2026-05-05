from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import urlretrieve


SUPPORTED_AUDIO_SUFFIXES = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac"}


@dataclass(frozen=True, slots=True)
class Soundtrack:
    track_id: str
    title: str
    source: str
    license_name: str | None
    credit: str | None
    bpm: int | None
    energy: float | None
    tags: tuple[str, ...]
    local_path: str | None = None
    download_url: str | None = None
    page_url: str | None = None


class SoundtrackLibrary:
    def __init__(self, tracks: list[Soundtrack] | None = None) -> None:
        self._tracks = tracks or []

    @classmethod
    def from_sources(cls, search_dirs: list[Path] | None = None, manifest_path: Path | None = None) -> SoundtrackLibrary:
        tracks: list[Soundtrack] = []
        seen: set[str] = set()

        if manifest_path and manifest_path.exists():
            manifest_tracks = _load_manifest_tracks(manifest_path)
            for track in manifest_tracks:
                if _track_keys(track) & seen:
                    continue
                seen.update(_track_keys(track))
                tracks.append(track)

        for directory in search_dirs or []:
            if not directory.exists():
                continue
            for file_path in sorted(directory.rglob("*")):
                if not file_path.is_file() or file_path.suffix.lower() not in SUPPORTED_AUDIO_SUFFIXES:
                    continue
                track = _track_from_file(file_path)
                if _track_keys(track) & seen:
                    continue
                seen.update(_track_keys(track))
                tracks.append(track)

        return cls(tracks)

    @property
    def tracks(self) -> list[Soundtrack]:
        return list(self._tracks)

    def has_tracks(self) -> bool:
        return bool(self._tracks)

    def has_available_tracks(self) -> bool:
        return any(_is_available(track) for track in self._tracks)

    def pick_for_concept(
        self,
        concept,
        used_track_ids: dict[str, int],
        fallback_bpm: int,
        min_bpm: int | None = None,
        prefer_high_energy: bool = False,
    ) -> tuple[Soundtrack | None, str | None]:
        available_tracks = [track for track in self._tracks if _is_available(track)]
        if not available_tracks:
            return None, None

        desired_tags = _desired_tags(concept.strategy, concept.style)
        target_bpm = max(fallback_bpm, min_bpm or fallback_bpm)
        ranked = sorted(
            available_tracks,
            key=lambda track: _track_score(track, desired_tags, used_track_ids, target_bpm, min_bpm, prefer_high_energy),
            reverse=True,
        )
        best = ranked[0]
        reason_parts = [f"Matched style tags {', '.join(sorted(set(desired_tags) & set(best.tags))) or 'none'}"]
        if best.bpm is not None:
            reason_parts.append(f"BPM {best.bpm} aligned with target rhythm {target_bpm}")
        if best.energy is not None:
            reason_parts.append(f"Energy {best.energy:.2f}")
        if used_track_ids.get(best.track_id, 0):
            reason_parts.append("Reused after exhausting stronger unused tracks")
        return best, "; ".join(reason_parts)

    def materialize_track(self, track: Soundtrack, destination: Path, dry_run: bool = False) -> Path | None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        if dry_run:
            return destination

        if track.local_path:
            source = Path(track.local_path)
            if not source.exists():
                return None
            shutil.copy2(source, destination)
            return destination

        if track.download_url:
            try:
                urlretrieve(track.download_url, destination)
                return destination
            except Exception:
                return None

        return None


def _load_manifest_tracks(manifest_path: Path) -> list[Soundtrack]:
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    raw_tracks = payload.get("tracks", payload if isinstance(payload, list) else [])
    result: list[Soundtrack] = []
    for index, entry in enumerate(raw_tracks, start=1):
        if not isinstance(entry, dict):
            continue
        title = str(entry.get("title") or entry.get("track_title") or f"Track {index}")
        local_path = _resolve_optional_path(manifest_path.parent, entry.get("path"))
        download_url, page_url = _manifest_urls(entry)
        track_id = str(entry.get("id") or entry.get("track_id") or local_path or download_url or f"manifest-track-{index}")
        tags = tuple(sorted({_normalize_tag(tag) for tag in entry.get("tags", []) if str(tag).strip()}))
        result.append(
            Soundtrack(
                track_id=track_id,
                title=title,
                source=str(entry.get("source") or "manifest"),
                license_name=str(entry.get("license") or "").strip() or None,
                credit=str(entry.get("credit") or "").strip() or None,
                bpm=_parse_optional_int(entry.get("bpm")),
                energy=_parse_optional_float(entry.get("energy")),
                tags=tags,
                local_path=local_path,
                download_url=download_url,
                page_url=page_url,
            )
        )
    return result


def _track_from_file(file_path: Path) -> Soundtrack:
    stem = file_path.stem.replace("_", " ").replace("-", " ")
    inferred_bpm = _infer_bpm_from_name(stem)
    inferred_tags = tuple(sorted({_normalize_tag(part) for part in stem.split() if part.strip()}))
    return Soundtrack(
        track_id=str(file_path.resolve()),
        title=file_path.stem,
        source="local_cache",
        license_name=None,
        credit=None,
        bpm=inferred_bpm,
        energy=None,
        tags=inferred_tags,
        local_path=str(file_path.resolve()),
    )


def _track_keys(track: Soundtrack) -> set[str]:
    return {value for value in (track.track_id, track.local_path, track.download_url) if value}


def _is_available(track: Soundtrack) -> bool:
    if track.local_path and Path(track.local_path).exists():
        return True
    return bool(track.download_url)


def _manifest_urls(entry: dict) -> tuple[str | None, str | None]:
    direct_url = str(entry.get("download_url") or entry.get("direct_url") or "").strip() or None
    page_url = str(entry.get("page_url") or entry.get("source_url") or "").strip() or None
    raw_url = str(entry.get("url") or "").strip() or None
    if raw_url:
        suffix = Path(urlparse(raw_url).path).suffix.lower()
        if suffix in SUPPORTED_AUDIO_SUFFIXES:
            direct_url = direct_url or raw_url
        else:
            page_url = page_url or raw_url
    return direct_url, page_url


def _desired_tags(strategy: str, style: str) -> tuple[str, ...]:
    tags = {"9x16", style, "social", "promo", "lifestyle"}
    if "nightlife" in strategy or "energy" in strategy:
        tags.update({"nightlife", "energy", "fast", "punchy"})
    if "people" in strategy:
        tags.update({"people", "human"})
    if "travel" in strategy:
        tags.update({"travel", "outdoor"})
    if "photo" in strategy:
        tags.update({"uplift", "clean"})
    if "broad" in strategy:
        tags.update({"broad", "upbeat"})
    return tuple(sorted(tags))


def _track_score(
    track: Soundtrack,
    desired_tags: tuple[str, ...],
    used_track_ids: dict[str, int],
    fallback_bpm: int,
    min_bpm: int | None = None,
    prefer_high_energy: bool = False,
) -> float:
    score = 0.0
    score += 0.45 * len(set(track.tags) & set(desired_tags))
    if track.bpm is not None:
        score += max(0.0, 1.4 - abs(track.bpm - fallback_bpm) / 36.0)
        if min_bpm is not None:
            score += min(track.bpm / max(min_bpm, 1), 1.15) * 0.45
            if track.bpm < min_bpm:
                score -= (min_bpm - track.bpm) / 18.0
    if track.energy is not None:
        score += (1.15 if prefer_high_energy else 0.8) * track.energy
    if track.source == "manifest":
        score += 0.3
    if track.license_name:
        score += 0.2
    score -= used_track_ids.get(track.track_id, 0) * 1.15
    return score


def _resolve_optional_path(base_dir: Path, value: object) -> str | None:
    if not value:
        return None
    path = Path(str(value)).expanduser()
    if not path.is_absolute():
        path = (base_dir / path).resolve()
    return str(path)


def _normalize_tag(value: object) -> str:
    return str(value).strip().lower().replace(" ", "_")


def _parse_optional_int(value: object) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _parse_optional_float(value: object) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _infer_bpm_from_name(name: str) -> int | None:
    tokens = [token for token in name.replace("-", " ").replace("_", " ").split() if token.isdigit()]
    for token in tokens:
        bpm = int(token)
        if 70 <= bpm <= 180:
            return bpm
    return None


def audio_suffix_for(track: Soundtrack) -> str:
    if track.local_path:
        return Path(track.local_path).suffix or ".wav"
    if track.download_url:
        suffix = Path(urlparse(track.download_url).path).suffix
        return suffix if suffix.lower() in SUPPORTED_AUDIO_SUFFIXES else ".mp3"
    return ".wav"
