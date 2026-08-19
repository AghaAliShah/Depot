"""
CRUD for FILES  =  Supabase Storage (the bytes) + Postgres (the metadata).

The mental model for every operation:

    CREATE  upload bytes -> ask the Edge Function to check them -> it writes the row
    READ    query the row(s) -> optionally download the bytes / make a signed URL
    UPDATE  either edit the row only, or replace the bytes and re-run the checks
    DELETE  remove the bytes, then remove the row

Note that on CREATE we never write the metadata row ourselves. The Edge
Function does it. That is the whole point: the server has the last word.
"""

from __future__ import annotations

import mimetypes
import re
import time
from pathlib import Path

from .edge import call_file_guard
from .supabase_client import get_client, get_settings

TABLE = "file_metadata"


class NotFoundError(LookupError):
    """No file matched what you typed."""


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _slugify(name: str) -> str:
    """'My Report (final).pdf' -> 'my-report-final.pdf' — safe for object keys."""
    stem = Path(name).stem
    suffix = Path(name).suffix.lower()
    stem = re.sub(r"[^a-zA-Z0-9]+", "-", stem).strip("-").lower() or "file"
    return f"{stem}{suffix}"


def _build_object_path(owner: str, file_name: str) -> str:
    """
    Storage has no real folders — a '/' in the key just looks like one.
    We prefix with the owner and a timestamp so two uploads never collide.
    """
    return f"{owner}/{int(time.time())}-{_slugify(file_name)}"


def _guess_mime(path: Path) -> str:
    return mimetypes.guess_type(path.name)[0] or "application/octet-stream"


def resolve(reference: str) -> dict:
    """
    Find one metadata row from whatever the user typed:
    a UUID, a full object_path, or just part of the file name.
    """
    client = get_client()

    # Looks like a UUID? Try the primary key first.
    if re.fullmatch(r"[0-9a-fA-F-]{36}", reference):
        found = client.table(TABLE).select("*").eq("id", reference).execute().data
        if found:
            return found[0]

    found = client.table(TABLE).select("*").eq("object_path", reference).execute().data
    if found:
        return found[0]

    found = (
        client.table(TABLE)
        .select("*")
        .ilike("file_name", f"%{reference}%")
        .order("created_at", desc=True)
        .execute()
        .data
    )
    if len(found) == 1:
        return found[0]
    if len(found) > 1:
        names = "\n".join(f"    {r['id']}  {r['object_path']}" for r in found[:10])
        raise NotFoundError(
            f"{len(found)} files match {reference!r}. Use the id:\n{names}"
        )

    raise NotFoundError(f"No file matches {reference!r}.")


# ---------------------------------------------------------------------------
# CREATE
# ---------------------------------------------------------------------------
def create_file(
    local_path: str | Path,
    *,
    owner: str | None = None,
    tags: list[str] | None = None,
) -> dict:
    """
    Upload a local file and return the metadata row the Edge Function created.

    Step 1  put the bytes in the bucket
    Step 2  call the Edge Function, which re-reads those bytes, validates them,
            adds checksum/category/size, and inserts the row.
            If it says no, it has already deleted the object again.
    """
    settings = get_settings()
    client = get_client()

    source = Path(local_path).expanduser()
    if not source.is_file():
        raise FileNotFoundError(f"No such file: {source}")

    owner = owner or settings.app_user
    object_path = _build_object_path(owner, source.name)
    mime_type = _guess_mime(source)

    # --- step 1: the bytes -------------------------------------------------
    client.storage.from_(settings.bucket).upload(
        object_path,
        source.read_bytes(),
        {"content-type": mime_type, "upsert": "false"},
    )

    # --- step 2: the server has the last word ------------------------------
    try:
        return call_file_guard(
            object_path=object_path,
            owner=owner,
            mime_type=mime_type,
            tags=tags,
        )
    except Exception:
        # Belt and braces: if the function never ran (network died, not
        # deployed), the object would be orphaned. Clean it up ourselves.
        try:
            client.storage.from_(settings.bucket).remove([object_path])
        except Exception:  # noqa: BLE001 - never mask the original error
            pass
        raise


# ---------------------------------------------------------------------------
# READ
# ---------------------------------------------------------------------------
def list_files(
    *,
    owner: str | None = None,
    search: str | None = None,
    limit: int = 50,
) -> list[dict]:
    """List metadata rows — this is a plain SQL query, no files are touched."""
    query = get_client().table(TABLE).select("*")
    if owner:
        query = query.eq("owner", owner)
    if search:
        query = query.ilike("file_name", f"%{search}%")
    return query.order("created_at", desc=True).limit(limit).execute().data


def get_file(reference: str) -> dict:
    """One metadata row."""
    return resolve(reference)


def download_file(reference: str, dest_dir: str | Path | None = None) -> Path:
    """Fetch the actual bytes out of Storage and write them to disk."""
    settings = get_settings()
    row = resolve(reference)

    target_dir = Path(dest_dir) if dest_dir else settings.download_dir
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / row["file_name"]

    data = get_client().storage.from_(row["bucket"]).download(row["object_path"])
    target.write_bytes(data)
    return target


def signed_url(reference: str, expires_in: int = 3600) -> str:
    """
    A temporary, shareable link to a file in a PRIVATE bucket.
    After `expires_in` seconds the link simply stops working.
    """
    row = resolve(reference)
    result = (
        get_client()
        .storage.from_(row["bucket"])
        .create_signed_url(row["object_path"], expires_in)
    )
    # The key has been spelled both ways across SDK versions.
    return result.get("signedURL") or result.get("signedUrl") or str(result)


# ---------------------------------------------------------------------------
# UPDATE
# ---------------------------------------------------------------------------
def update_metadata(
    reference: str,
    *,
    file_name: str | None = None,
    owner: str | None = None,
    tags: list[str] | None = None,
) -> dict:
    """Change the row only. The bytes in the bucket are untouched."""
    row = resolve(reference)

    changes: dict = {}
    if file_name is not None:
        changes["file_name"] = file_name
    if owner is not None:
        changes["owner"] = owner
    if tags is not None:
        changes["tags"] = tags

    if not changes:
        return row

    return (
        get_client()
        .table(TABLE)
        .update(changes)
        .eq("id", row["id"])
        .execute()
        .data[0]
    )


def replace_file(reference: str, local_path: str | Path) -> dict:
    """
    Swap in new bytes at the SAME object_path, then re-run the Edge Function.

    Because the function upserts on object_path, the existing row is updated
    in place — new checksum, new size, new category — and the file keeps its id.
    """
    settings = get_settings()
    row = resolve(reference)

    source = Path(local_path).expanduser()
    if not source.is_file():
        raise FileNotFoundError(f"No such file: {source}")

    mime_type = _guess_mime(source)

    # `update` overwrites an object that already exists (upload would refuse).
    get_client().storage.from_(row["bucket"]).update(
        row["object_path"],
        source.read_bytes(),
        {"content-type": mime_type, "upsert": "true"},
    )

    return call_file_guard(
        object_path=row["object_path"],
        owner=row["owner"],
        mime_type=mime_type,
        tags=row.get("tags") or [],
    )


# ---------------------------------------------------------------------------
# DELETE
# ---------------------------------------------------------------------------
def delete_file(reference: str) -> dict:
    """
    Remove the bytes, then the row.

    Bytes first: if the row went first and the storage call then failed, you
    would have a file nobody can find — an orphan. This order fails safely.
    """
    row = resolve(reference)
    client = get_client()

    client.storage.from_(row["bucket"]).remove([row["object_path"]])
    client.table(TABLE).delete().eq("id", row["id"]).execute()
    return row
