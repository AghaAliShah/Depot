"""
CRUD for a plain TABLE  =  Supabase Database only. No files involved.

This is the simpler half of the project, and a good place to start reading.
Every method is one call on `client.table("notes")`:

    .insert({...})            INSERT
    .select("*")              SELECT
    .update({...}).eq(...)    UPDATE ... WHERE
    .delete().eq(...)         DELETE ... WHERE

`.execute()` is what actually sends the HTTP request. Everything before it is
just building the query. The result has a `.data` attribute holding a list of
rows as plain dicts.
"""

from __future__ import annotations

import re

from .supabase_client import get_client, get_settings

TABLE = "notes"


class NoteNotFound(LookupError):
    pass


def resolve(reference: str) -> dict:
    """Find one note by UUID, or by a fragment of its title."""
    client = get_client()

    if re.fullmatch(r"[0-9a-fA-F-]{36}", reference):
        found = client.table(TABLE).select("*").eq("id", reference).execute().data
        if found:
            return found[0]

    found = (
        client.table(TABLE)
        .select("*")
        .ilike("title", f"%{reference}%")
        .order("created_at", desc=True)
        .execute()
        .data
    )
    if len(found) == 1:
        return found[0]
    if len(found) > 1:
        listing = "\n".join(f"    {r['id']}  {r['title']}" for r in found[:10])
        raise NoteNotFound(
            f"{len(found)} notes match {reference!r}. Use the id:\n{listing}"
        )

    raise NoteNotFound(f"No note matches {reference!r}.")


# ---------------------------------------------------------------------------
# CREATE
# ---------------------------------------------------------------------------
def create_note(
    title: str,
    content: str = "",
    *,
    author: str | None = None,
    tags: list[str] | None = None,
    is_pinned: bool = False,
) -> dict:
    author = author or get_settings().app_user
    payload = {
        "title": title,
        "content": content,
        "author": author,
        "tags": tags or [],
        "is_pinned": is_pinned,
    }
    # .select() after .insert() asks Postgres to hand the new row straight back,
    # including the columns the database filled in (id, created_at).
    return get_client().table(TABLE).insert(payload).execute().data[0]


# ---------------------------------------------------------------------------
# READ
# ---------------------------------------------------------------------------
def list_notes(
    *,
    author: str | None = None,
    search: str | None = None,
    pinned_only: bool = False,
    limit: int = 50,
) -> list[dict]:
    query = get_client().table(TABLE).select("*")
    if author:
        query = query.eq("author", author)
    if pinned_only:
        query = query.eq("is_pinned", True)
    if search:
        # or_ builds  WHERE title ILIKE ... OR content ILIKE ...
        query = query.or_(f"title.ilike.%{search}%,content.ilike.%{search}%")
    return (
        query.order("is_pinned", desc=True)
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
        .data
    )


def get_note(reference: str) -> dict:
    return resolve(reference)


# ---------------------------------------------------------------------------
# UPDATE
# ---------------------------------------------------------------------------
def update_note(
    reference: str,
    *,
    title: str | None = None,
    content: str | None = None,
    tags: list[str] | None = None,
    is_pinned: bool | None = None,
) -> dict:
    note = resolve(reference)

    changes: dict = {}
    if title is not None:
        changes["title"] = title
    if content is not None:
        changes["content"] = content
    if tags is not None:
        changes["tags"] = tags
    if is_pinned is not None:
        changes["is_pinned"] = is_pinned

    if not changes:
        return note

    return (
        get_client()
        .table(TABLE)
        .update(changes)
        .eq("id", note["id"])
        .execute()
        .data[0]
    )


# ---------------------------------------------------------------------------
# DELETE
# ---------------------------------------------------------------------------
def delete_note(reference: str) -> dict:
    note = resolve(reference)
    get_client().table(TABLE).delete().eq("id", note["id"]).execute()
    return note
