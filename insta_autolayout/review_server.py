from __future__ import annotations

import json
import threading
import webbrowser
from dataclasses import dataclass
from functools import partial
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from .promo_exporter import refresh_review_assets


@dataclass(slots=True)
class ReviewContext:
    input_dir: Path | None
    manual_overrides_path: Path | None
    shared_state_dir: Path | None = None
    reviewer_id: str = "rami"
    project_id: str = "trybe"
    reviewer_explicit: bool = False


def serve_review_batch(
    batch_dir: Path,
    open_browser: bool = True,
    shared_state_dir: Path | None = None,
    reviewer_id: str | None = None,
    project_id: str = "trybe",
) -> None:
    batch_dir = batch_dir.expanduser().resolve()
    if not batch_dir.exists():
        raise SystemExit(f"Review batch does not exist: {batch_dir}")
    refresh_review_assets(batch_dir)
    context = _load_context(batch_dir)
    context.shared_state_dir = shared_state_dir
    context.reviewer_id = reviewer_id or context.reviewer_id
    context.reviewer_explicit = reviewer_id is not None
    context.project_id = project_id
    recorder = FeedbackRecorder(batch_dir, context)
    handler = partial(_ReviewHandler, directory=str(batch_dir), recorder=recorder)
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    url = f"http://127.0.0.1:{server.server_port}/index.html"
    print(f"review server: {url}", flush=True)
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nreview server stopped")
    finally:
        server.server_close()


class FeedbackRecorder:
    def __init__(self, batch_dir: Path, context: ReviewContext) -> None:
        self.batch_dir = batch_dir
        self.context = context
        self.feedback_path = batch_dir / "feedback_events.jsonl"
        self.structured_feedback_path = batch_dir / "review_events_v2.jsonl"
        self.summary_path = batch_dir / "feedback_summary.json"
        self._lock = threading.Lock()

    def record(self, payload: dict) -> dict:
        event = {
            "concept_id": payload.get("concept_id"),
            "target_type": payload.get("target_type"),
            "action": payload.get("action"),
            "source_file": payload.get("source_file"),
            "clip_token": payload.get("clip_token"),
            "note": payload.get("note"),
        }
        with self._lock:
            self.feedback_path.parent.mkdir(parents=True, exist_ok=True)
            with self.feedback_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(event, ensure_ascii=True) + "\n")
            overrides_state = self._apply_to_manual_overrides(event)
            summary = self._feedback_summary(overrides_state)
            self.summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
        return summary

    def record_structured(self, payload: dict) -> dict:
        event = self._normalize_structured_event(payload)
        with self._lock:
            self.structured_feedback_path.parent.mkdir(parents=True, exist_ok=True)
            with self.structured_feedback_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(event, ensure_ascii=True) + "\n")
            overrides_state = self._apply_structured_to_manual_overrides(event)
            summary = self._feedback_summary(overrides_state)
            summary["structured_event_count"] = _jsonl_count(self.structured_feedback_path)
            shared_summary = self._record_shared_event(event)
            if shared_summary:
                summary["shared_state"] = shared_summary
            self.summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
        return summary

    def clear_structured(self, *, reviewer_id: str, batch_id: str, concept_id: str | None = None) -> dict:
        with self._lock:
            removed_local = 0
            if self.structured_feedback_path.exists():
                kept_lines: list[str] = []
                for line in self.structured_feedback_path.read_text(encoding="utf-8").splitlines():
                    if not line.strip():
                        continue
                    raw = json.loads(line)
                    same_reviewer = str(raw.get("reviewer_id") or "") == reviewer_id
                    same_batch = str(raw.get("batch_id") or self.batch_dir.name) == batch_id
                    same_concept = concept_id is None or str(raw.get("concept_id") or "") == concept_id
                    if same_reviewer and same_batch and same_concept:
                        removed_local += 1
                    else:
                        kept_lines.append(line)
                if kept_lines:
                    self.structured_feedback_path.write_text("\n".join(kept_lines) + "\n", encoding="utf-8")
                else:
                    self.structured_feedback_path.unlink(missing_ok=True)
            shared_summary = None
            if self.context.shared_state_dir is not None or self.context.project_id:
                from .shared_state import SharedReviewState, shared_review_backend_available

                if self.context.shared_state_dir is not None or shared_review_backend_available():
                    state = SharedReviewState(self.context.shared_state_dir, project_id=self.context.project_id)
                    state.delete_events(batch_id=batch_id, reviewer_id=reviewer_id, concept_id=concept_id)
                    shared_summary = state.rebuild_summary(batch_id)
            overrides_state = self._load_manual_overrides()
            summary = self._feedback_summary(overrides_state)
            summary["structured_event_count"] = _jsonl_count(self.structured_feedback_path)
            summary["deleted_local_events"] = removed_local
            if shared_summary is not None:
                summary["shared_state"] = {
                    "root": (
                        str(self.context.shared_state_dir / "review_state")
                        if self.context.shared_state_dir is not None
                        else f"supabase://review_events/{self.context.project_id}"
                    ),
                    "event_count": shared_summary.get("event_count"),
                }
            self.summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
            return summary

    def _normalize_structured_event(self, payload: dict) -> dict:
        target = payload.get("target") if isinstance(payload.get("target"), dict) else {}
        event = {
            "schema_version": 2,
            "event_id": payload.get("event_id"),
            "created_at": payload.get("created_at"),
            "reviewer_id": self.context.reviewer_id if self.context.reviewer_explicit else payload.get("reviewer_id") or self.context.reviewer_id,
            "project_id": payload.get("project_id") or self.context.project_id,
            "batch_id": payload.get("batch_id") or self.batch_dir.name,
            "concept_id": payload.get("concept_id"),
            "variant_id": payload.get("variant_id"),
            "target": target,
            "status": payload.get("status") or "unreviewed",
            "rating": payload.get("rating"),
            "reason_tags": payload.get("reason_tags") if isinstance(payload.get("reason_tags"), list) else [],
            "note": payload.get("note"),
            "generation_context": payload.get("generation_context") if isinstance(payload.get("generation_context"), dict) else {},
        }
        try:
            from .review_models import ReviewEvent

            return ReviewEvent.from_dict(event).to_dict()
        except Exception:
            return event

    def _record_shared_event(self, event: dict) -> dict | None:
        from .shared_state import SharedReviewState, shared_review_backend_available

        if self.context.shared_state_dir is None and not shared_review_backend_available():
            return None
        try:
            state = SharedReviewState(self.context.shared_state_dir, project_id=self.context.project_id)
            saved = state.append_event(event)
            batch_id = str(saved.get("batch_id") or self.batch_dir.name)
            summary = state.rebuild_summary(batch_id)
            state.rebuild_manual_overrides()
            return {
                "root": state.backend_label,
                "event_count": summary.get("event_count"),
                "summary_path": (
                    str(state.review_state_dir / "summaries" / f"{batch_id}.summary.json")
                    if state.review_state_dir is not None
                    else None
                ),
            }
        except Exception as exc:
            return {"error": str(exc)}

    def _apply_structured_to_manual_overrides(self, event: dict) -> dict:
        target = event.get("target") if isinstance(event.get("target"), dict) else {}
        target_type = str(target.get("type") or "")
        source_file = _normalize_name(target.get("source_file"))
        clip_token = _normalize_name(target.get("clip_token"))
        status = str(event.get("status") or "")
        tags = {str(tag) for tag in event.get("reason_tags", [])}
        rating = event.get("rating")
        action = None
        if target_type == "source_file" and source_file:
            if status in {"approved", "shortlist"} or "source_high_quality" in tags:
                action = "prefer_source"
            elif status == "reject" or "off_brand" in tags or "source_overused" in tags:
                action = "avoid_source"
        elif target_type == "clip" and clip_token:
            if status in {"approved", "shortlist"} or "good_trim" in tags or "good_action" in tags:
                action = "good_clip"
            elif status in {"reject", "needs_edit"} or "bad_trim" in tags:
                action = "bad_clip"
            if isinstance(rating, (int, float)) and rating > 0:
                action = "good_clip"
            elif isinstance(rating, (int, float)) and rating < 0:
                action = "bad_clip"

        if action is None:
            return self._load_manual_overrides()
        return self._apply_to_manual_overrides(
            {
                "action": action,
                "source_file": source_file,
                "clip_token": clip_token,
            }
        )

    def _apply_to_manual_overrides(self, event: dict) -> dict:
        path = self.context.manual_overrides_path
        if path is None:
            return {}
        raw = self._load_manual_overrides()
        raw.setdefault("prefer_files", [])
        raw.setdefault("avoid_files", [])
        raw.setdefault("clip_ratings", {})

        action = str(event.get("action") or "")
        source_file = _normalize_name(event.get("source_file"))
        clip_token = _normalize_name(event.get("clip_token"))
        prefer = {_normalize_name(item) for item in raw.get("prefer_files", []) if item}
        avoid = {_normalize_name(item) for item in raw.get("avoid_files", []) if item}
        clip_ratings = {
            _normalize_name(str(key)): float(value)
            for key, value in dict(raw.get("clip_ratings", {})).items()
            if key and isinstance(value, (int, float))
        }

        if action == "prefer_source" and source_file:
            prefer.add(source_file)
            avoid.discard(source_file)
        elif action == "avoid_source" and source_file:
            avoid.add(source_file)
            prefer.discard(source_file)
        elif action == "good_clip" and clip_token:
            clip_ratings[clip_token] = clip_ratings.get(clip_token, 0.0) + 1.0
        elif action == "bad_clip" and clip_token:
            clip_ratings[clip_token] = clip_ratings.get(clip_token, 0.0) - 1.0

        raw["prefer_files"] = sorted(prefer)
        raw["avoid_files"] = sorted(avoid)
        raw["clip_ratings"] = {key: round(value, 2) for key, value in sorted(clip_ratings.items())}
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(raw, indent=2), encoding="utf-8")
        return raw

    def _load_manual_overrides(self) -> dict:
        path = self.context.manual_overrides_path
        if path is None or not path.exists():
            return {}
        return json.loads(path.read_text(encoding="utf-8"))

    def _feedback_summary(self, overrides_state: dict) -> dict:
        events = []
        if self.feedback_path.exists():
            events = [json.loads(line) for line in self.feedback_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        return {
            "event_count": len(events),
            "prefer_file_count": len(overrides_state.get("prefer_files", [])),
            "avoid_file_count": len(overrides_state.get("avoid_files", [])),
            "clip_rating_count": len(overrides_state.get("clip_ratings", {})),
            "manual_overrides_path": str(self.context.manual_overrides_path) if self.context.manual_overrides_path else None,
        }


class _ReviewHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, recorder: FeedbackRecorder, directory: str, **kwargs) -> None:
        self.recorder = recorder
        super().__init__(*args, directory=directory, **kwargs)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path not in {"/api/feedback", "/api/review/events", "/api/review/clear"}:
            self.send_error(HTTPStatus.NOT_FOUND, "Unknown endpoint")
            return
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
        if parsed.path == "/api/review/events":
            summary = self.recorder.record_structured(payload)
        elif parsed.path == "/api/review/clear":
            summary = self.recorder.clear_structured(
                reviewer_id=str(payload.get("reviewer_id") or self.recorder.context.reviewer_id),
                batch_id=str(payload.get("batch_id") or self.recorder.batch_dir.name),
                concept_id=str(payload.get("concept_id") or "").strip() or None,
            )
        else:
            summary = self.recorder.record(payload)
        body = json.dumps({"ok": True, "summary": summary}).encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _load_context(batch_dir: Path) -> ReviewContext:
    context_path = batch_dir / "review_context.json"
    if not context_path.exists():
        inferred_input_dir = _infer_input_dir(batch_dir)
        inferred_overrides = (inferred_input_dir / "manual-overrides.json") if inferred_input_dir else None
        return ReviewContext(input_dir=inferred_input_dir, manual_overrides_path=inferred_overrides)
    raw = json.loads(context_path.read_text(encoding="utf-8"))
    input_dir = Path(raw["input_dir"]).expanduser().resolve() if raw.get("input_dir") else None
    overrides_path = Path(raw["manual_overrides_path"]).expanduser().resolve() if raw.get("manual_overrides_path") else None
    if overrides_path is None and input_dir is not None:
        overrides_path = input_dir / "manual-overrides.json"
    return ReviewContext(input_dir=input_dir, manual_overrides_path=overrides_path)


def _normalize_name(value: str | None) -> str | None:
    if value is None:
        return None
    return str(value).replace("\\", "/")


def _jsonl_count(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())


def _infer_input_dir(batch_dir: Path) -> Path | None:
    report_paths = sorted(batch_dir.glob("video_*/report.json"))
    for report_path in report_paths:
        try:
            raw = json.loads(report_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        timeline = raw.get("timeline")
        if not isinstance(timeline, list):
            continue
        source_files = [Path(str(item.get("source_file"))).expanduser().resolve() for item in timeline if isinstance(item, dict) and item.get("source_file")]
        if not source_files:
            continue
        parents = {path.parent for path in source_files}
        if len(parents) == 1:
            return next(iter(parents))
    return None
