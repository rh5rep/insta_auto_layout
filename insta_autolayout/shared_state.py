from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from .feedback_adapter import derive_manual_overrides
from .review_models import ReviewEvent


class SharedReviewState:
    def __init__(self, shared_root: str | Path) -> None:
        self.review_state_dir = _review_state_dir(Path(shared_root).expanduser())

    def append_event(self, event: ReviewEvent | dict[str, Any]) -> dict[str, Any]:
        review_event = event if isinstance(event, ReviewEvent) else ReviewEvent.from_dict(event)
        data = review_event.to_dict()
        event_path = self.event_path(data["batch_id"], data["reviewer_id"])
        event_path.parent.mkdir(parents=True, exist_ok=True)
        with event_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(data, ensure_ascii=True, sort_keys=True) + "\n")
        return data

    def event_path(self, batch_id: str, reviewer_id: str) -> Path:
        return self.review_state_dir / "events" / batch_id / f"{reviewer_id}.jsonl"

    def load_events(self, batch_id: str) -> list[dict[str, Any]]:
        return load_events_for_batch(self.review_state_dir, batch_id)

    def rebuild_summary(self, batch_id: str) -> dict[str, Any]:
        return rebuild_summary(self.review_state_dir, batch_id)

    def rebuild_manual_overrides(self, batch_id: str | None = None) -> dict[str, Any]:
        events = self.load_events(batch_id) if batch_id else load_all_events(self.review_state_dir)
        overrides = derive_manual_overrides(events)
        path = self.review_state_dir / "derived" / "manual-overrides.generated.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(overrides, ensure_ascii=True, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return overrides


def load_events_for_batch(shared_root_or_review_state: str | Path, batch_id: str) -> list[dict[str, Any]]:
    review_state_dir = _review_state_dir(Path(shared_root_or_review_state).expanduser())
    events_dir = review_state_dir / "events" / batch_id
    if not events_dir.exists():
        return []
    events: list[dict[str, Any]] = []
    for path in sorted(events_dir.glob("*.jsonl")):
        events.extend(_load_jsonl(path))
    return sorted(events, key=lambda event: (str(event.get("created_at") or ""), str(event.get("event_id") or "")))


def load_all_events(shared_root_or_review_state: str | Path) -> list[dict[str, Any]]:
    review_state_dir = _review_state_dir(Path(shared_root_or_review_state).expanduser())
    events: list[dict[str, Any]] = []
    for path in sorted((review_state_dir / "events").glob("*/*.jsonl")):
        events.extend(_load_jsonl(path))
    return sorted(events, key=lambda event: (str(event.get("created_at") or ""), str(event.get("event_id") or "")))


def rebuild_summary(shared_root_or_review_state: str | Path, batch_id: str) -> dict[str, Any]:
    review_state_dir = _review_state_dir(Path(shared_root_or_review_state).expanduser())
    events = load_events_for_batch(review_state_dir, batch_id)
    summary = summarize_events(events, batch_id)
    path = review_state_dir / "summaries" / f"{batch_id}.summary.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(summary, ensure_ascii=True, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return summary


def rebuild_manual_overrides(shared_root_or_review_state: str | Path, batch_id: str | None = None) -> dict[str, Any]:
    state = SharedReviewState(shared_root_or_review_state)
    return state.rebuild_manual_overrides(batch_id=batch_id)


def summarize_events(events: list[dict[str, Any]], batch_id: str) -> dict[str, Any]:
    statuses = Counter(str(event.get("status") or "unknown") for event in events)
    target_types = Counter(str((event.get("target") or {}).get("type") or "unknown") for event in events)
    reviewers = Counter(str(event.get("reviewer_id") or "unknown") for event in events)
    return {
        "schema_version": 1,
        "batch_id": batch_id,
        "event_count": len(events),
        "reviewer_counts": dict(sorted(reviewers.items())),
        "status_counts": dict(sorted(statuses.items())),
        "target_type_counts": dict(sorted(target_types.items())),
        "latest_event_at": max((str(event.get("created_at") or "") for event in events), default=None),
    }


def _review_state_dir(path: Path) -> Path:
    return path if path.name == "review_state" else path / "review_state"


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    events = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            events.append(json.loads(line))
    return events
