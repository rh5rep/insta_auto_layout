from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import hashlib
import json
from typing import Any
from uuid import uuid4


SCHEMA_VERSION = 2
KNOWN_TARGET_TYPES = {
    "concept",
    "source_file",
    "clip",
    "soundtrack",
    "text_overlay",
    "brand_card",
}


@dataclass
class ReviewEvent:
    reviewer_id: str
    project_id: str
    batch_id: str
    target: dict[str, Any]
    status: str = "unreviewed"
    schema_version: int = SCHEMA_VERSION
    event_id: str | None = None
    created_at: str | None = None
    concept_id: str | None = None
    variant_id: str | None = None
    rating: float | int | None = None
    reason_tags: list[str] = field(default_factory=list)
    note: str | None = None
    generation_context: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.reviewer_id = _required_str(self.reviewer_id, "reviewer_id")
        self.project_id = _required_str(self.project_id, "project_id")
        self.batch_id = _required_str(self.batch_id, "batch_id")
        self.status = _required_str(self.status, "status")
        self.target = normalize_target(self.target)
        self.reason_tags = [str(tag) for tag in self.reason_tags if tag]
        self.generation_context = dict(self.generation_context or {})
        if self.event_id is not None:
            self.event_id = str(self.event_id)
        if self.created_at is not None:
            self.created_at = str(self.created_at)

    def with_defaults(self) -> "ReviewEvent":
        if not self.event_id:
            self.event_id = make_event_id(
                self.reviewer_id,
                project_id=self.project_id,
                batch_id=self.batch_id,
                concept_id=self.concept_id,
                variant_id=self.variant_id,
                target=self.target,
            )
        if not self.created_at:
            self.created_at = utc_now_iso()
        return self

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self.with_defaults())
        return {key: value for key, value in data.items() if value is not None}

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "ReviewEvent":
        return cls(
            schema_version=int(raw.get("schema_version", SCHEMA_VERSION)),
            event_id=raw.get("event_id"),
            created_at=raw.get("created_at"),
            reviewer_id=raw.get("reviewer_id", ""),
            project_id=raw.get("project_id", ""),
            batch_id=raw.get("batch_id", ""),
            concept_id=raw.get("concept_id"),
            variant_id=raw.get("variant_id"),
            target=raw.get("target") or {},
            status=raw.get("status", "unreviewed"),
            rating=raw.get("rating"),
            reason_tags=list(raw.get("reason_tags") or []),
            note=raw.get("note"),
            generation_context=dict(raw.get("generation_context") or {}),
        )


def normalize_target(target: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(target, dict):
        raise TypeError("target must be a dict")
    normalized = dict(target)
    target_type = normalized.get("type") or normalized.get("target_type")
    if not target_type:
        raise ValueError("target.type is required")
    target_type = str(target_type)
    if target_type not in KNOWN_TARGET_TYPES:
        raise ValueError(f"Unsupported target.type: {target_type}")
    normalized["type"] = target_type
    normalized.pop("target_type", None)
    return normalized


def make_event_id(
    reviewer_id: str,
    *,
    project_id: str | None = None,
    batch_id: str | None = None,
    concept_id: str | None = None,
    variant_id: str | None = None,
    target: dict[str, Any] | None = None,
) -> str:
    if project_id and batch_id and target:
        payload = {
            "reviewer_id": reviewer_id,
            "project_id": project_id,
            "batch_id": batch_id,
            "concept_id": concept_id or "",
            "variant_id": variant_id or "",
            "target": _stable_target(target),
        }
        digest = hashlib.sha1(json.dumps(payload, sort_keys=True, ensure_ascii=True).encode("utf-8")).hexdigest()[:16]
        return f"state-{digest}"
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{timestamp}-{reviewer_id}-{uuid4().hex[:8]}"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _required_str(value: Any, field_name: str) -> str:
    if value is None or str(value) == "":
        raise ValueError(f"{field_name} is required")
    return str(value)


def _stable_target(target: dict[str, Any]) -> dict[str, Any]:
    target_type = str(target.get("type") or "")
    if target_type == "concept":
        return {"type": "concept"}
    if target_type == "brand_card":
        return {"type": "brand_card", "role": str(target.get("role") or "brand_card")}
    if target_type == "source_file":
        return {"type": "source_file", "source_file": str(target.get("source_file") or "")}
    if target_type == "clip":
        return {
            "type": "clip",
            "clip_token": str(target.get("clip_token") or ""),
            "source_file": str(target.get("source_file") or ""),
        }
    return {"type": target_type}
