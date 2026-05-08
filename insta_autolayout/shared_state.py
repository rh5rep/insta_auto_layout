from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from .feedback_adapter import derive_manual_overrides
from .review_models import ReviewEvent
from .supabase_state import SupabaseReviewStateClient, remote_review_state_available


class SharedReviewState:
    def __init__(self, shared_root: str | Path | None, project_id: str | None = None, enable_remote: bool = True) -> None:
        self.project_id = project_id
        self.review_state_dir = _review_state_dir(Path(shared_root).expanduser()) if shared_root else None
        self.remote = SupabaseReviewStateClient.from_env() if enable_remote else None

    def append_event(self, event: ReviewEvent | dict[str, Any]) -> dict[str, Any]:
        review_event = event if isinstance(event, ReviewEvent) else ReviewEvent.from_dict(event)
        data = review_event.to_dict()
        if self.review_state_dir is not None:
            event_path = self.event_path(data["batch_id"], data["reviewer_id"])
            event_path.parent.mkdir(parents=True, exist_ok=True)
            existing: list[dict[str, Any]] = []
            if event_path.exists():
                existing = _load_jsonl(event_path)
            replaced = False
            for index, raw in enumerate(existing):
                if str(raw.get("event_id") or "") == str(data.get("event_id") or ""):
                    existing[index] = data
                    replaced = True
                    break
            if not replaced:
                existing.append(data)
            lines = [json.dumps(item, ensure_ascii=True, sort_keys=True) for item in existing]
            event_path.write_text(("\n".join(lines) + "\n") if lines else "", encoding="utf-8")
        if self.remote is not None:
            self.remote.append_review_event(data)
        return data

    def event_path(self, batch_id: str, reviewer_id: str) -> Path:
        if self.review_state_dir is None:
            raise ValueError("No local shared review path is configured")
        return self.review_state_dir / "events" / batch_id / f"{reviewer_id}.jsonl"

    def load_events(self, batch_id: str) -> list[dict[str, Any]]:
        if self.remote is not None and self.project_id:
            return self.remote.list_review_events(self.project_id, batch_id)
        if self.review_state_dir is None:
            return []
        return load_events_for_batch(self.review_state_dir, batch_id)

    def rebuild_summary(self, batch_id: str) -> dict[str, Any]:
        events = self.load_events(batch_id)
        summary = summarize_events(events, batch_id)
        if self.review_state_dir is not None:
            path = self.review_state_dir / "summaries" / f"{batch_id}.summary.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(summary, ensure_ascii=True, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return summary

    def rebuild_manual_overrides(self, batch_id: str | None = None) -> dict[str, Any]:
        if self.remote is not None and self.project_id:
            events = self.remote.list_review_events(self.project_id, batch_id=batch_id)
        elif self.review_state_dir is not None:
            events = self.load_events(batch_id) if batch_id else load_all_events(self.review_state_dir)
        else:
            events = []
        overrides = derive_manual_overrides(events)
        if self.review_state_dir is not None:
            path = self.review_state_dir / "derived" / "manual-overrides.generated.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(overrides, ensure_ascii=True, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        if self.remote is not None and self.project_id and batch_id is None:
            self.remote.upsert_derived_feedback(self.project_id, overrides)
        return overrides

    def delete_events(self, *, batch_id: str, reviewer_id: str, concept_id: str | None = None) -> int:
        deleted = 0
        if self.review_state_dir is not None:
            event_path = self.event_path(batch_id, reviewer_id)
            if event_path.exists():
                kept_lines: list[str] = []
                for line in event_path.read_text(encoding="utf-8").splitlines():
                    if not line.strip():
                        continue
                    raw = json.loads(line)
                    if concept_id and str(raw.get("concept_id") or "") != concept_id:
                        kept_lines.append(line)
                        continue
                    deleted += 1
                if kept_lines:
                    event_path.write_text("\n".join(kept_lines) + "\n", encoding="utf-8")
                else:
                    event_path.unlink(missing_ok=True)
        if self.remote is not None and self.project_id:
            self.remote.delete_review_events(
                project_id=self.project_id,
                batch_id=batch_id,
                reviewer_id=reviewer_id,
                concept_id=concept_id,
            )
        self.rebuild_summary(batch_id)
        self.rebuild_manual_overrides()
        return deleted

    @property
    def backend_label(self) -> str:
        if self.review_state_dir is not None:
            return str(self.review_state_dir)
        if self.remote is not None and self.project_id:
            return f"supabase://review_events/{self.project_id}"
        return "unconfigured"


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


def shared_review_backend_available() -> bool:
    return remote_review_state_available()
