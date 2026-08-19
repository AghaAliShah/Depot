"""
Offline smoke tests — no Supabase project, no network, no .env needed.

    python tests/test_offline.py

They swap the real Supabase client for a tiny fake that records what the app
asked it to do. That proves the CRUD wiring is right (correct table, correct
filters, correct order of operations) before you ever touch a real project.

Run them after you change anything in app/.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import storage_crud, table_crud  # noqa: E402
from app.config import Settings  # noqa: E402

SETTINGS = Settings(
    url="https://fake.supabase.co",
    anon_key="fake-anon-key",
    bucket="documents",
    edge_function="file-guard",
    app_user="tester",
    download_dir=Path(__file__).resolve().parent / "_tmp_downloads",
)

FILE_ROW = {
    "id": "11111111-1111-1111-1111-111111111111",
    "bucket": "documents",
    "object_path": "tester/1700000000-hello.txt",
    "file_name": "hello.txt",
    "mime_type": "text/plain",
    "size_bytes": 12,
    "size_human": "12 B",
    "owner": "tester",
    "tags": ["demo"],
    "category": "document",
    "checksum_sha256": "abc123",
    "validated": True,
    "created_at": "2026-08-18T10:00:00+00:00",
}

NOTE_ROW = {
    "id": "22222222-2222-2222-2222-222222222222",
    "title": "First note",
    "content": "hello",
    "author": "tester",
    "tags": [],
    "is_pinned": False,
    "created_at": "2026-08-18T10:00:00+00:00",
    "updated_at": "2026-08-18T10:00:00+00:00",
}


# ---------------------------------------------------------------------------
# The fake
# ---------------------------------------------------------------------------
class FakeResult:
    def __init__(self, data):
        self.data = data


class FakeQuery:
    """
    Records every call, and applies the eq/ilike filters for real so that
    `resolve()` behaves the way it would against Postgres.
    """

    WRITES = {"insert", "update", "delete"}

    def __init__(self, log: list, rows: list):
        self.log = log
        self.rows = rows
        self.filters: list[tuple] = []
        self.is_write = False

    def _record(self, name, *args):
        self.log.append((name, *args))
        if name in self.WRITES:
            self.is_write = True
        elif name in ("eq", "ilike"):
            self.filters.append((name, *args))
        return self

    def _matching_rows(self):
        if self.is_write:
            return self.rows
        rows = self.rows
        for kind, column, value in self.filters:
            if kind == "eq":
                rows = [r for r in rows if r.get(column) == value]
            else:  # ilike '%needle%'
                needle = str(value).strip("%").lower()
                rows = [r for r in rows if needle in str(r.get(column, "")).lower()]
        return rows

    def select(self, *a):
        return self._record("select", *a)

    def eq(self, *a):
        return self._record("eq", *a)

    def ilike(self, *a):
        return self._record("ilike", *a)

    def or_(self, *a):
        return self._record("or_", *a)

    def order(self, *a, **kw):
        return self._record("order", *a)

    def limit(self, *a):
        return self._record("limit", *a)

    def insert(self, *a):
        return self._record("insert", *a)

    def update(self, *a):
        return self._record("update", *a)

    def delete(self, *a):
        return self._record("delete", *a)

    def execute(self):
        self.log.append(("execute",))
        return FakeResult(self._matching_rows())


class FakeBucket:
    def __init__(self, log: list, payload: bytes = b"hello world"):
        self.log = log
        self.payload = payload

    def upload(self, path, file, options=None):
        self.log.append(("storage.upload", path, len(file)))

    def update(self, path, file, options=None):
        self.log.append(("storage.update", path, len(file)))

    def download(self, path):
        self.log.append(("storage.download", path))
        return self.payload

    def remove(self, paths):
        self.log.append(("storage.remove", tuple(paths)))

    def create_signed_url(self, path, expires_in):
        self.log.append(("storage.signed_url", path, expires_in))
        return {"signedURL": f"https://fake/{path}?token=xyz&exp={expires_in}"}


class FakeStorage:
    def __init__(self, log):
        self.log = log

    def from_(self, bucket):
        self.log.append(("storage.from_", bucket))
        return FakeBucket(self.log)


class FakeClient:
    def __init__(self, log, rows):
        self.log = log
        self.rows = rows
        self.storage = FakeStorage(log)

    def table(self, name):
        self.log.append(("table", name))
        return FakeQuery(self.log, self.rows)


# ---------------------------------------------------------------------------
class StorageCrudTests(unittest.TestCase):
    def setUp(self):
        self.log: list = []
        self.client = FakeClient(self.log, [dict(FILE_ROW)])
        patches = [
            mock.patch.object(storage_crud, "get_client", lambda: self.client),
            mock.patch.object(storage_crud, "get_settings", lambda: SETTINGS),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def test_object_path_is_namespaced_and_slugified(self):
        path = storage_crud._build_object_path("tester", "My Report (final).PDF")
        self.assertTrue(path.startswith("tester/"))
        self.assertTrue(path.endswith("-my-report-final.pdf"))

    def test_create_uploads_then_calls_the_edge_function(self):
        source = Path(__file__).resolve().parent.parent / "sample_files" / "hello.txt"
        with mock.patch.object(
            storage_crud, "call_file_guard", return_value=dict(FILE_ROW)
        ) as guard:
            row = storage_crud.create_file(source, tags=["demo"])

        self.assertEqual(row["file_name"], "hello.txt")
        # bytes first...
        self.assertEqual(self.log[1][0], "storage.upload")
        # ...then the server has the last word
        guard.assert_called_once()
        self.assertEqual(guard.call_args.kwargs["owner"], "tester")
        self.assertEqual(guard.call_args.kwargs["tags"], ["demo"])

    def test_create_cleans_up_when_the_edge_function_fails(self):
        source = Path(__file__).resolve().parent.parent / "sample_files" / "hello.txt"
        with mock.patch.object(storage_crud, "call_file_guard", side_effect=RuntimeError("no")):
            with self.assertRaises(RuntimeError):
                storage_crud.create_file(source)

        self.assertTrue(
            any(entry[0] == "storage.remove" for entry in self.log),
            "an orphaned object must be removed",
        )

    def test_list_filters_by_owner_and_name(self):
        storage_crud.list_files(owner="tester", search="hello", limit=5)
        self.assertIn(("table", "file_metadata"), self.log)
        self.assertIn(("eq", "owner", "tester"), self.log)
        self.assertIn(("ilike", "file_name", "%hello%"), self.log)

    def test_delete_removes_bytes_before_the_row(self):
        storage_crud.delete_file(FILE_ROW["id"])
        order = [entry[0] for entry in self.log]
        self.assertLess(order.index("storage.remove"), order.index("delete"))

    def test_signed_url_handles_either_spelling(self):
        url = storage_crud.signed_url(FILE_ROW["id"], 60)
        self.assertIn("token=", url)

    def test_replace_reruns_validation_on_the_same_path(self):
        source = Path(__file__).resolve().parent.parent / "sample_files" / "hello.txt"
        with mock.patch.object(
            storage_crud, "call_file_guard", return_value=dict(FILE_ROW)
        ) as guard:
            storage_crud.replace_file(FILE_ROW["id"], source)

        self.assertTrue(any(e[0] == "storage.update" for e in self.log))
        self.assertEqual(guard.call_args.kwargs["object_path"], FILE_ROW["object_path"])

    def test_ambiguous_reference_is_reported_not_guessed(self):
        self.client.rows = [dict(FILE_ROW), dict(FILE_ROW, id="33333333-3333-3333-3333-333333333333")]
        with self.assertRaises(storage_crud.NotFoundError):
            storage_crud.resolve("hello")


class TableCrudTests(unittest.TestCase):
    def setUp(self):
        self.log: list = []
        self.client = FakeClient(self.log, [dict(NOTE_ROW)])
        patches = [
            mock.patch.object(table_crud, "get_client", lambda: self.client),
            mock.patch.object(table_crud, "get_settings", lambda: SETTINGS),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def test_create_note_defaults_the_author_to_the_app_user(self):
        table_crud.create_note("First note", "hello")
        insert = next(e for e in self.log if e[0] == "insert")
        self.assertEqual(insert[1]["author"], "tester")
        self.assertEqual(insert[1]["tags"], [])

    def test_list_notes_searches_title_and_content(self):
        table_crud.list_notes(search="hello", pinned_only=True)
        self.assertIn(("or_", "title.ilike.%hello%,content.ilike.%hello%"), self.log)
        self.assertIn(("eq", "is_pinned", True), self.log)

    def test_update_with_nothing_to_change_is_a_no_op(self):
        before = len(self.log)
        result = table_crud.update_note(NOTE_ROW["id"])
        self.assertEqual(result["id"], NOTE_ROW["id"])
        self.assertFalse(any(e[0] == "update" for e in self.log[before:]))

    def test_delete_note_targets_the_resolved_id(self):
        table_crud.delete_note("First")
        self.assertIn(("eq", "id", NOTE_ROW["id"]), self.log)

    def test_missing_note_raises(self):
        self.client.rows = []
        with self.assertRaises(table_crud.NoteNotFound):
            table_crud.get_note("nothing-like-this")


if __name__ == "__main__":
    unittest.main(verbosity=2)
