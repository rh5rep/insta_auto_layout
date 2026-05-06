from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from insta_autolayout.overrides import load_manual_overrides
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


if __name__ == "__main__":
    unittest.main()
