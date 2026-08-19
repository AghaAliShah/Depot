"""
Calling the Edge Function from Python.

An Edge Function is just an HTTPS endpoint:

    POST https://<project>.supabase.co/functions/v1/file-guard
    Authorization: Bearer <anon key>
    Content-Type: application/json
    {...json body...}

The Supabase SDK has `client.functions.invoke(...)`, but it hides the HTTP
status code and the error body, which are the two things you most want to see
while you are learning. So we use httpx directly — it is only a few lines and
nothing is hidden.
"""

from __future__ import annotations

from typing import Any

import httpx

from .supabase_client import get_settings

TIMEOUT_SECONDS = 60.0


class EdgeFunctionError(RuntimeError):
    """The function was reached but refused the request (or blew up)."""

    def __init__(self, status_code: int, payload: Any):
        self.status_code = status_code
        self.payload = payload
        super().__init__(self._describe())

    def _describe(self) -> str:
        if isinstance(self.payload, dict):
            problems = self.payload.get("problems")
            if problems:
                bullets = "\n".join(f"    - {p}" for p in problems)
                return f"Edge Function rejected the file:\n{bullets}"
            if self.payload.get("error"):
                return f"Edge Function error ({self.status_code}): {self.payload['error']}"
        return f"Edge Function returned HTTP {self.status_code}: {self.payload}"


def _headers() -> dict[str, str]:
    settings = get_settings()
    return {
        # verify_jwt is on, and the anon key is itself a JWT, so this satisfies it.
        "Authorization": f"Bearer {settings.anon_key}",
        "apikey": settings.anon_key,
        "Content-Type": "application/json",
    }


def call_file_guard(
    *,
    object_path: str,
    owner: str,
    mime_type: str | None = None,
    tags: list[str] | None = None,
) -> dict:
    """
    Ask the server to validate the object that was just uploaded.

    On success returns the metadata row the function wrote to Postgres.
    On rejection raises EdgeFunctionError — and the file has already been
    deleted from Storage by the function itself.
    """
    settings = get_settings()
    body = {
        "bucket": settings.bucket,
        "object_path": object_path,
        "owner": owner,
        "mime_type": mime_type,
        "tags": tags or [],
    }

    try:
        response = httpx.post(
            settings.edge_function_url,
            json=body,
            headers=_headers(),
            timeout=TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as exc:
        raise EdgeFunctionError(0, {"error": f"could not reach the function: {exc}"}) from exc

    try:
        payload = response.json()
    except ValueError:
        payload = response.text

    if response.status_code >= 400:
        raise EdgeFunctionError(response.status_code, payload)

    return payload["metadata"]


def health() -> dict:
    """GET the function, to prove it is deployed and reachable."""
    settings = get_settings()
    response = httpx.get(
        settings.edge_function_url, headers=_headers(), timeout=TIMEOUT_SECONDS
    )
    response.raise_for_status()
    return response.json()
