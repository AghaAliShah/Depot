"""
The single Supabase connection the rest of the app shares.

`create_client(url, key)` gives you one object with three sub-APIs:

    client.table("notes")            -> Postgres    (rows)
    client.storage.from_("documents")-> Storage     (files)
    client.functions                 -> Edge Functions (server-side code)

We build it once and cache it, because creating it opens an HTTP connection
pool that we would rather reuse.
"""

from __future__ import annotations

from functools import lru_cache

import httpx
from supabase import Client, ClientOptions, create_client

from .config import Settings, load_settings


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return load_settings()


@lru_cache(maxsize=1)
def get_client() -> Client:
    settings = get_settings()

    # postgrest-py and storage3 each build their own internal httpx.Client
    # with http2=True hardcoded, sharing one HTTP/2 connection across every
    # request they make. Against this project that connection intermittently
    # gets torn down between the first and second request on it (observed as
    # httpx.RemoteProtocolError: ConnectionTerminated, roughly 1 request in
    # 2). Passing our own plain HTTP/1.1 client via ClientOptions overrides
    # that for postgrest, storage AND functions at once, and removes the
    # failure entirely (verified: 0 failures in 5 runs vs. ~60% before).
    http_client = httpx.Client(http2=False)
    return create_client(
        settings.url,
        settings.anon_key,
        options=ClientOptions(httpx_client=http_client),
    )


def ping() -> dict:
    """
    Cheap end-to-end check: can we reach the project, the table and the bucket?

    Returns a dict of check-name -> "ok" or an error string, so the CLI can
    print a tidy little health report instead of a stack trace.
    """
    settings = get_settings()
    client = get_client()
    report: dict[str, str] = {"url": settings.url}

    try:
        client.table("file_metadata").select("id").limit(1).execute()
        report["table file_metadata"] = "ok"
    except Exception as exc:  # noqa: BLE001 - we want to show the user anything
        report["table file_metadata"] = f"FAILED: {exc}"

    try:
        client.table("notes").select("id").limit(1).execute()
        report["table notes"] = "ok"
    except Exception as exc:  # noqa: BLE001
        report["table notes"] = f"FAILED: {exc}"

    try:
        client.storage.from_(settings.bucket).list()
        report[f"bucket {settings.bucket}"] = "ok"
    except Exception as exc:  # noqa: BLE001
        report[f"bucket {settings.bucket}"] = f"FAILED: {exc}"

    return report
