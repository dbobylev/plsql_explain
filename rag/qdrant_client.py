from __future__ import annotations

import json
import os
from typing import Any
from urllib import error, request


class QdrantClient:
    """Minimal Qdrant HTTP client without external SDK dependency."""

    def __init__(self) -> None:
        url = os.environ.get("QDRANT_URL")
        if not url:
            raise ValueError("QDRANT_URL is required")
        self._base_url = url.rstrip("/")
        self._api_key = os.environ.get("QDRANT_API_KEY", "")

    def get_collection(self, name: str) -> dict[str, Any] | None:
        try:
            return self._request("GET", f"/collections/{name}")
        except error.HTTPError as exc:
            if exc.code == 404:
                return None
            raise

    def create_collection(self, name: str, vector_size: int, distance: str = "Cosine") -> dict[str, Any]:
        return self._request(
            "PUT",
            f"/collections/{name}",
            {
                "vectors": {
                    "size": vector_size,
                    "distance": distance,
                }
            },
        )

    def upsert_points(
        self,
        collection: str,
        points: list[dict[str, Any]],
        wait: bool = True,
    ) -> dict[str, Any]:
        suffix = "?wait=true" if wait else ""
        return self._request(
            "PUT",
            f"/collections/{collection}/points{suffix}",
            {"points": points},
        )

    def search(
        self,
        collection: str,
        vector: list[float],
        limit: int = 5,
        query_filter: dict[str, Any] | None = None,
        with_payload: bool = True,
    ) -> list[dict[str, Any]]:
        payload: dict[str, Any] = {
            "vector": vector,
            "limit": limit,
            "with_payload": with_payload,
        }
        if query_filter:
            payload["filter"] = query_filter
        response = self._request(
            "POST",
            f"/collections/{collection}/points/search",
            payload,
        )
        return response.get("result", [])

    def _request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        body = None
        headers = {
            "Accept": "application/json",
        }
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if self._api_key:
            headers["api-key"] = self._api_key

        req = request.Request(
            url=f"{self._base_url}{path}",
            data=body,
            headers=headers,
            method=method,
        )
        with request.urlopen(req, timeout=60) as response:
            raw = response.read().decode("utf-8")
        if not raw:
            return {}
        return json.loads(raw)
