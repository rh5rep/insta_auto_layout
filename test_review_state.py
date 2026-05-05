from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from insta_autolayout.review_models import ReviewEvent
from insta_autolayout.shared_state import SharedReviewState


class SharedReviewStateTest(unittest.TestCase):
    def test_appends_events_under_review_state_and_derives_overrides(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state = SharedReviewState(tmp)
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
            state = SharedReviewState(review_state)
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


if __name__ == "__main__":
    unittest.main()
