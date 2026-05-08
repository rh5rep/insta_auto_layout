from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .cloud_env import supabase_config, supabase_enabled


class SupabaseStateError(RuntimeError):
    pass


@dataclass(slots=True)
class SupabaseReviewStateClient:
    base_url: str
    api_key: str

    @classmethod
    def from_env(cls) -> "SupabaseReviewStateClient | None":
        config = supabase_config()
        if not config.configured:
            return None
        return cls(base_url=config.url.rstrip("/"), api_key=config.api_key)

    def append_review_event(self, event: dict[str, Any]) -> dict[str, Any]:
        rows = self._request(
            "POST",
            "review_events",
            query={"on_conflict": "event_id"},
            body=event,
            headers={"Prefer": "resolution=merge-duplicates,return=representation"},
        )
        if isinstance(rows, list) and rows:
            return dict(rows[0])
        if isinstance(rows, dict):
            return dict(rows)
        return event

    def list_review_events(self, project_id: str, batch_id: str | None = None) -> list[dict[str, Any]]:
        query = {
            "select": "*",
            "project_id": f"eq.{project_id}",
            "order": "created_at.asc,event_id.asc",
        }
        if batch_id:
            query["batch_id"] = f"eq.{batch_id}"
        rows = self._request("GET", "review_events", query=query)
        return [dict(row) for row in rows] if isinstance(rows, list) else []

    def upsert_derived_feedback(self, project_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        body = {
            "project_id": project_id,
            "payload": payload,
            "updated_at": _utc_now_iso(),
        }
        rows = self._request(
            "POST",
            "derived_feedback",
            query={"on_conflict": "project_id"},
            body=body,
            headers={"Prefer": "resolution=merge-duplicates,return=representation"},
        )
        if isinstance(rows, list) and rows:
            return dict(rows[0])
        if isinstance(rows, dict):
            return dict(rows)
        return body

    def get_derived_feedback(self, project_id: str) -> dict[str, Any] | None:
        rows = self._request(
            "GET",
            "derived_feedback",
            query={
                "select": "payload,updated_at",
                "project_id": f"eq.{project_id}",
                "limit": "1",
            },
        )
        if not isinstance(rows, list) or not rows:
            return None
        row = dict(rows[0])
        payload = row.get("payload")
        if isinstance(payload, dict):
            return payload
        return None

    def delete_review_events(
        self,
        *,
        project_id: str,
        batch_id: str,
        reviewer_id: str,
        concept_id: str | None = None,
    ) -> None:
        query = {
            "project_id": f"eq.{project_id}",
            "batch_id": f"eq.{batch_id}",
            "reviewer_id": f"eq.{reviewer_id}",
        }
        if concept_id:
            query["concept_id"] = f"eq.{concept_id}"
        self._request("DELETE", "review_events", query=query, headers={"Prefer": "return=minimal"})

    def _request(
        self,
        method: str,
        table: str,
        query: dict[str, str] | None = None,
        body: dict[str, Any] | list[dict[str, Any]] | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        url = f"{self.base_url}/rest/v1/{table}"
        if query:
            url = f"{url}?{urlencode(query)}"
        request_headers = {
            "apikey": self.api_key,
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
        }
        payload: bytes | None = None
        if body is not None:
            payload = json.dumps(body, ensure_ascii=True).encode("utf-8")
            request_headers["Content-Type"] = "application/json"
        if headers:
            request_headers.update(headers)
        request = Request(url, data=payload, headers=request_headers, method=method)
        try:
            with urlopen(request, timeout=20) as response:
                raw = response.read()
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise SupabaseStateError(f"Supabase {method} {table} failed: HTTP {exc.code} {detail}") from exc
        except URLError as exc:
            raise SupabaseStateError(f"Supabase {method} {table} failed: {exc.reason}") from exc
        if not raw:
            return None
        try:
            return json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise SupabaseStateError(f"Supabase {method} {table} returned invalid JSON") from exc


def remote_review_state_available() -> bool:
    return supabase_enabled()


def fetch_remote_derived_feedback(project_id: str) -> dict[str, Any] | None:
    client = SupabaseReviewStateClient.from_env()
    if client is None:
        return None
    return client.get_derived_feedback(project_id)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
