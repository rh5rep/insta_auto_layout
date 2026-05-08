from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from insta_autolayout.overrides import load_manual_overrides
from insta_autolayout.review_models import ReviewEvent
from insta_autolayout.shared_state import SharedReviewState


class SharedReviewStateTest(unittest.TestCase):
    def test_appends_events_under_review_state_and_derives_overrides(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state = SharedReviewState(tmp, enable_remote=False)
            state.append_event(
                ReviewEvent(
                    reviewer_id="rami",
                    project_id="trybe",
                    batch_id="batch_01",
                    target={"type": "source_file", "source_file": "clips/a.mov"},
                    status="approved",
                    reason_tags=["source_high_quality"],
                )
            )
            state.append_event(
                {
                    "reviewer_id": "max",
                    "project_id": "trybe",
                    "batch_id": "batch_01",
                    "target": {"type": "clip", "source_file": "clips/b.mov", "source_start": 4.2, "source_end": 5.1},
                    "status": "needs_edit",
                    "reason_tags": ["bad_trim"],
                }
            )

            event_path = Path(tmp) / "review_state" / "events" / "batch_01" / "rami.jsonl"
            event = json.loads(event_path.read_text(encoding="utf-8").strip())
            self.assertEqual(event["schema_version"], 2)
            self.assertIn("event_id", event)
            self.assertIn("created_at", event)

            summary = state.rebuild_summary("batch_01")
            self.assertEqual(summary["event_count"], 2)
            self.assertEqual(summary["target_type_counts"], {"clip": 1, "source_file": 1})

            overrides = state.rebuild_manual_overrides("batch_01")
            self.assertEqual(overrides["prefer_files"], ["clips/a.mov"])
            self.assertEqual(overrides["avoid_files"], [])
            self.assertEqual(overrides["clip_ratings"], {"clips/b.mov@4.2-5.1": -1})

    def test_accepts_review_state_path_directly(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            review_state = Path(tmp) / "review_state"
            state = SharedReviewState(review_state, enable_remote=False)
            state.append_event(
                {
                    "reviewer_id": "rami",
                    "project_id": "trybe",
                    "batch_id": "batch_02",
                    "target": {"type": "source_file", "source_file": "a.mov"},
                    "status": "reject",
                }
            )
            self.assertTrue((review_state / "events" / "batch_02" / "rami.jsonl").exists())

    def test_merges_generated_feedback_with_manual_overrides(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            album = root / "album"
            album.mkdir(parents=True, exist_ok=True)
            shared_root = root / "shared"
            derived_path = shared_root / "review_state" / "derived" / "manual-overrides.generated.json"
            derived_path.parent.mkdir(parents=True, exist_ok=True)
            derived_path.write_text(
                json.dumps(
                    {
                        "prefer_files": ["clips/generated_good.mov"],
                        "avoid_files": ["clips/conflict.mov", "clips/generated_bad.mov"],
                        "clip_ratings": {
                            "clips/generated_bad.mov@4.2-5.1": -1,
                            "clips/conflict.mov@1.0-2.0": -2,
                        },
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
            manual_path = album / "manual-overrides.json"
            manual_path.write_text(
                json.dumps(
                    {
                        "prefer_files": ["clips/conflict.mov"],
                        "clip_ratings": {"clips/conflict.mov@1.0-2.0": 2},
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )

            overrides = load_manual_overrides(album, None, shared_state_dir=shared_root)
            self.assertEqual(overrides["derived_path"], str(derived_path))
            self.assertEqual(overrides["manual_path"], str(manual_path.resolve()))
            self.assertEqual(overrides["prefer_files"], {"clips/generated_good.mov", "clips/conflict.mov"})
            self.assertEqual(overrides["avoid_files"], {"clips/generated_bad.mov"})
            self.assertEqual(
                overrides["clip_ratings"],
                {
                    "clips/generated_bad.mov@4.2-5.1": -1.0,
                    "clips/conflict.mov@1.0-2.0": 2.0,
                },
            )
            self.assertTrue(overrides["using_generated_feedback"])

    def test_uses_remote_generated_feedback_when_available(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            album = root / "album"
            album.mkdir(parents=True, exist_ok=True)
            manual_path = album / "manual-overrides.json"
            manual_path.write_text(json.dumps({"avoid_files": ["clips/local_bad.mov"]}, indent=2), encoding="utf-8")
            with (
                patch("insta_autolayout.overrides.remote_review_state_available", return_value=True),
                patch(
                    "insta_autolayout.overrides.fetch_remote_derived_feedback",
                    return_value={
                        "prefer_files": ["clips/remote_good.mov"],
                        "clip_ratings": {"clips/remote_good.mov@1.0-2.0": 1},
                    },
                ),
            ):
                overrides = load_manual_overrides(album, None, shared_state_dir=None, project_id="trybe")
            self.assertEqual(overrides["derived_path"], "supabase://derived_feedback/trybe")
            self.assertEqual(overrides["prefer_files"], {"clips/remote_good.mov"})
            self.assertEqual(overrides["avoid_files"], {"clips/local_bad.mov"})
            self.assertEqual(overrides["clip_ratings"], {"clips/remote_good.mov@1.0-2.0": 1.0})

    def test_delete_events_removes_local_batch_feedback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state = SharedReviewState(tmp, project_id="trybe", enable_remote=False)
            state.append_event(
                {
                    "reviewer_id": "rami",
                    "project_id": "trybe",
                    "batch_id": "batch_03",
                    "concept_id": "video_01",
                    "target": {"type": "concept"},
                    "status": "approved",
                }
            )
            state.append_event(
                {
                    "reviewer_id": "rami",
                    "project_id": "trybe",
                    "batch_id": "batch_03",
                    "concept_id": "video_02",
                    "target": {"type": "concept"},
                    "status": "reject",
                }
            )
            deleted = state.delete_events(batch_id="batch_03", reviewer_id="rami", concept_id="video_01")
            self.assertEqual(deleted, 1)
            events = state.load_events("batch_03")
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]["concept_id"], "video_02")

    def test_resaving_same_target_overwrites_instead_of_appending(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state = SharedReviewState(tmp, project_id="trybe", enable_remote=False)
            first = state.append_event(
                {
                    "reviewer_id": "rami",
                    "project_id": "trybe",
                    "batch_id": "batch_04",
                    "concept_id": "video_01",
                    "target": {"type": "brand_card", "role": "outro"},
                    "status": "reject",
                    "rating": -2,
                }
            )
            second = state.append_event(
                {
                    "reviewer_id": "rami",
                    "project_id": "trybe",
                    "batch_id": "batch_04",
                    "concept_id": "video_01",
                    "target": {"type": "brand_card", "role": "outro"},
                    "status": "approved",
                    "rating": 1,
                }
            )
            self.assertEqual(first["event_id"], second["event_id"])
            events = state.load_events("batch_04")
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]["rating"], 1)
            self.assertEqual(events[0]["status"], "approved")


if __name__ == "__main__":
    unittest.main()
