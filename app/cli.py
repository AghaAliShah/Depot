"""
The terminal app.

Two ways to drive it:

    python main.py menu                 <- interactive, guided, easiest to explore
    python main.py files upload a.pdf   <- one-shot commands, good for scripting

Run `python main.py --help` (or `python main.py files --help`) to see everything.
"""

from __future__ import annotations

import argparse
import sys

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from . import edge, storage_crud, table_crud
from .config import ConfigError
from .supabase_client import get_settings, ping

console = Console()


# ---------------------------------------------------------------------------
# printing helpers
# ---------------------------------------------------------------------------
def _print_file_row(row: dict) -> None:
    table = Table(show_header=False, box=None, pad_edge=False)
    table.add_column(style="dim", justify="right")
    table.add_column()
    for label, key in [
        ("id", "id"),
        ("file name", "file_name"),
        ("object path", "object_path"),
        ("owner", "owner"),
        ("size", "size_human"),
        ("category", "category"),
        ("mime type", "mime_type"),
        ("checksum", "checksum_sha256"),
        ("validated", "validated"),
        ("created", "created_at"),
    ]:
        table.add_row(label, str(row.get(key, "")))
    table.add_row("tags", ", ".join(row.get("tags") or []) or "-")
    console.print(Panel(table, title=row.get("file_name", "file"), border_style="cyan"))


def _print_files(rows: list[dict]) -> None:
    if not rows:
        console.print("[yellow]No files yet. Try:  python main.py files upload <path>[/]")
        return
    table = Table(title=f"{len(rows)} file(s)", header_style="bold cyan")
    table.add_column("id", style="dim", no_wrap=True)
    table.add_column("file name")
    table.add_column("size", justify="right")
    table.add_column("category")
    table.add_column("owner")
    table.add_column("tags")
    table.add_column("uploaded")
    for row in rows:
        table.add_row(
            str(row["id"])[:8],
            row.get("file_name", ""),
            row.get("size_human") or "",
            row.get("category") or "",
            row.get("owner") or "",
            ", ".join(row.get("tags") or []),
            (row.get("created_at") or "")[:19].replace("T", " "),
        )
    console.print(table)


def _print_note_row(note: dict) -> None:
    body = note.get("content") or "[dim](empty)[/]"
    meta = (
        f"[dim]id[/] {note['id']}\n"
        f"[dim]author[/] {note.get('author')}   "
        f"[dim]pinned[/] {note.get('is_pinned')}   "
        f"[dim]tags[/] {', '.join(note.get('tags') or []) or '-'}\n"
        f"[dim]updated[/] {(note.get('updated_at') or '')[:19].replace('T', ' ')}"
    )
    console.print(
        Panel(f"{body}\n\n{meta}", title=note.get("title", "note"), border_style="green")
    )


def _print_notes(rows: list[dict]) -> None:
    if not rows:
        console.print("[yellow]No notes yet. Try:  python main.py notes add \"My title\"[/]")
        return
    table = Table(title=f"{len(rows)} note(s)", header_style="bold green")
    table.add_column("id", style="dim", no_wrap=True)
    table.add_column("pin", justify="center")
    table.add_column("title")
    table.add_column("content")
    table.add_column("author")
    table.add_column("tags")
    for row in rows:
        preview = (row.get("content") or "").replace("\n", " ")
        table.add_row(
            str(row["id"])[:8],
            "*" if row.get("is_pinned") else "",
            row.get("title", ""),
            preview[:40] + ("..." if len(preview) > 40 else ""),
            row.get("author", ""),
            ", ".join(row.get("tags") or []),
        )
    console.print(table)


def _tags(value: str | None) -> list[str] | None:
    if value is None:
        return None
    return [t.strip() for t in value.split(",") if t.strip()]


# ---------------------------------------------------------------------------
# command handlers
# ---------------------------------------------------------------------------
def cmd_check(_: argparse.Namespace) -> None:
    console.print("[bold]Checking your Supabase setup...[/]")
    report = ping()
    table = Table(show_header=False, box=None)
    for name, status in report.items():
        colour = "green" if status == "ok" or name == "url" else "red"
        table.add_row(name, f"[{colour}]{status}[/]")
    console.print(table)

    try:
        info = edge.health()
        console.print(
            f"[green]edge function {get_settings().edge_function}[/] ok "
            f"(max {info['max_bytes'] // 1024 // 1024} MB, "
            f"{len(info['allowed_extensions'])} allowed extensions)"
        )
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]edge function: FAILED: {exc}[/]")
        console.print("[dim]  -> not deployed yet? see docs/EDGE_FUNCTION.md[/]")


def cmd_files_upload(args: argparse.Namespace) -> None:
    console.print(f"Uploading [cyan]{args.path}[/] ...")
    row = storage_crud.create_file(args.path, owner=args.owner, tags=_tags(args.tags))
    console.print("[green]Accepted by the Edge Function.[/]")
    _print_file_row(row)


def cmd_files_list(args: argparse.Namespace) -> None:
    _print_files(storage_crud.list_files(owner=args.owner, search=args.search, limit=args.limit))


def cmd_files_show(args: argparse.Namespace) -> None:
    _print_file_row(storage_crud.get_file(args.reference))


def cmd_files_download(args: argparse.Namespace) -> None:
    path = storage_crud.download_file(args.reference, args.to)
    console.print(f"[green]Saved to[/] {path}")


def cmd_files_link(args: argparse.Namespace) -> None:
    url = storage_crud.signed_url(args.reference, args.expires)
    console.print(f"[green]Valid for {args.expires} seconds:[/]\n{url}")


def cmd_files_update(args: argparse.Namespace) -> None:
    row = storage_crud.update_metadata(
        args.reference, file_name=args.name, owner=args.owner, tags=_tags(args.tags)
    )
    console.print("[green]Metadata updated.[/]")
    _print_file_row(row)


def cmd_files_replace(args: argparse.Namespace) -> None:
    console.print(f"Replacing bytes with [cyan]{args.path}[/] ...")
    row = storage_crud.replace_file(args.reference, args.path)
    console.print("[green]Replaced and re-validated.[/]")
    _print_file_row(row)


def cmd_files_delete(args: argparse.Namespace) -> None:
    row = storage_crud.get_file(args.reference)
    if not args.yes:
        console.print(f"About to delete [red]{row['file_name']}[/] ({row['object_path']})")
        if input("Type 'yes' to confirm: ").strip().lower() != "yes":
            console.print("Cancelled.")
            return
    storage_crud.delete_file(row["id"])
    console.print(f"[green]Deleted[/] {row['file_name']} (bytes + metadata row)")


def cmd_notes_add(args: argparse.Namespace) -> None:
    note = table_crud.create_note(
        args.title,
        args.content or "",
        author=args.author,
        tags=_tags(args.tags),
        is_pinned=args.pin,
    )
    console.print("[green]Note created.[/]")
    _print_note_row(note)


def cmd_notes_list(args: argparse.Namespace) -> None:
    _print_notes(
        table_crud.list_notes(
            author=args.author, search=args.search, pinned_only=args.pinned, limit=args.limit
        )
    )


def cmd_notes_show(args: argparse.Namespace) -> None:
    _print_note_row(table_crud.get_note(args.reference))


def cmd_notes_update(args: argparse.Namespace) -> None:
    pinned = True if args.pin else (False if args.unpin else None)
    note = table_crud.update_note(
        args.reference,
        title=args.title,
        content=args.content,
        tags=_tags(args.tags),
        is_pinned=pinned,
    )
    console.print("[green]Note updated.[/]")
    _print_note_row(note)


def cmd_notes_delete(args: argparse.Namespace) -> None:
    note = table_crud.get_note(args.reference)
    if not args.yes:
        console.print(f"About to delete note [red]{note['title']}[/]")
        if input("Type 'yes' to confirm: ").strip().lower() != "yes":
            console.print("Cancelled.")
            return
    table_crud.delete_note(note["id"])
    console.print(f"[green]Deleted[/] {note['title']}")


# ---------------------------------------------------------------------------
# interactive menu
# ---------------------------------------------------------------------------
MENU = """
[bold cyan]FILES[/] (Storage + metadata, guarded by the Edge Function)
  1  upload a file          2  list files           3  show one file
  4  download a file        5  temporary share link 6  edit metadata
  7  replace the file       8  delete a file

[bold green]NOTES[/] (plain table CRUD)
  9  add a note            10  list notes          11  show a note
 12  edit a note           13  delete a note

  c  check the connection   q  quit
"""


def _ask(prompt: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    answer = input(f"{prompt}{suffix}: ").strip()
    return answer or default


def cmd_menu(_: argparse.Namespace) -> None:
    ns = argparse.Namespace
    while True:
        console.print(Panel(MENU.strip(), title="Depot", border_style="blue"))
        choice = input("choose: ").strip().lower()
        try:
            if choice == "q":
                return
            elif choice == "c":
                cmd_check(ns())
            elif choice == "1":
                cmd_files_upload(ns(path=_ask("local file path"), owner=None,
                                    tags=_ask("tags (comma separated)", "") or None))
            elif choice == "2":
                cmd_files_list(ns(owner=None, search=_ask("filter by name", "") or None, limit=50))
            elif choice == "3":
                cmd_files_show(ns(reference=_ask("file id or name")))
            elif choice == "4":
                cmd_files_download(ns(reference=_ask("file id or name"), to=None))
            elif choice == "5":
                cmd_files_link(ns(reference=_ask("file id or name"),
                                  expires=int(_ask("seconds valid", "3600"))))
            elif choice == "6":
                cmd_files_update(ns(reference=_ask("file id or name"),
                                    name=_ask("new display name", "") or None,
                                    owner=_ask("new owner", "") or None,
                                    tags=_ask("new tags (comma separated)", "") or None))
            elif choice == "7":
                cmd_files_replace(ns(reference=_ask("file id or name"),
                                     path=_ask("path of the replacement file")))
            elif choice == "8":
                cmd_files_delete(ns(reference=_ask("file id or name"), yes=False))
            elif choice == "9":
                cmd_notes_add(ns(title=_ask("title"), content=_ask("content", ""),
                                 author=None, tags=_ask("tags", "") or None,
                                 pin=_ask("pin it? y/n", "n").lower().startswith("y")))
            elif choice == "10":
                cmd_notes_list(ns(author=None, search=_ask("search", "") or None,
                                  pinned=False, limit=50))
            elif choice == "11":
                cmd_notes_show(ns(reference=_ask("note id or title")))
            elif choice == "12":
                cmd_notes_update(ns(reference=_ask("note id or title"),
                                    title=_ask("new title", "") or None,
                                    content=_ask("new content", "") or None,
                                    tags=_ask("new tags", "") or None,
                                    pin=False, unpin=False))
            elif choice == "13":
                cmd_notes_delete(ns(reference=_ask("note id or title"), yes=False))
            else:
                console.print("[yellow]Unknown choice.[/]")
        except Exception as exc:  # noqa: BLE001 - a menu should never crash out
            console.print(f"[red]{type(exc).__name__}:[/] {exc}")
        console.print()


# ---------------------------------------------------------------------------
# argument parser
# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python main.py",
        description="CRUD app for Supabase Storage, Database and Edge Functions.",
    )
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("check", help="verify .env, tables, bucket and edge function").set_defaults(
        func=cmd_check
    )
    sub.add_parser("menu", help="interactive menu (start here)").set_defaults(func=cmd_menu)

    # ---- files -----------------------------------------------------------
    files = sub.add_parser("files", help="CRUD on files (Storage + metadata)")
    fsub = files.add_subparsers(dest="files_command", required=True)

    p = fsub.add_parser("upload", help="CREATE: upload + validate via Edge Function")
    p.add_argument("path")
    p.add_argument("--owner")
    p.add_argument("--tags", help="comma separated")
    p.set_defaults(func=cmd_files_upload)

    p = fsub.add_parser("list", help="READ: list metadata rows")
    p.add_argument("--owner")
    p.add_argument("--search")
    p.add_argument("--limit", type=int, default=50)
    p.set_defaults(func=cmd_files_list)

    p = fsub.add_parser("show", help="READ: one file's metadata")
    p.add_argument("reference", help="id, object path, or part of the file name")
    p.set_defaults(func=cmd_files_show)

    p = fsub.add_parser("download", help="READ: fetch the bytes to disk")
    p.add_argument("reference")
    p.add_argument("--to", help="destination folder")
    p.set_defaults(func=cmd_files_download)

    p = fsub.add_parser("link", help="READ: temporary signed URL")
    p.add_argument("reference")
    p.add_argument("--expires", type=int, default=3600, help="seconds")
    p.set_defaults(func=cmd_files_link)

    p = fsub.add_parser("update", help="UPDATE: metadata only")
    p.add_argument("reference")
    p.add_argument("--name")
    p.add_argument("--owner")
    p.add_argument("--tags", help="comma separated")
    p.set_defaults(func=cmd_files_update)

    p = fsub.add_parser("replace", help="UPDATE: swap the bytes, re-validate")
    p.add_argument("reference")
    p.add_argument("path")
    p.set_defaults(func=cmd_files_replace)

    p = fsub.add_parser("delete", help="DELETE: bytes + metadata row")
    p.add_argument("reference")
    p.add_argument("--yes", action="store_true", help="skip the confirmation")
    p.set_defaults(func=cmd_files_delete)

    # ---- notes -----------------------------------------------------------
    notes = sub.add_parser("notes", help="CRUD on a plain table")
    nsub = notes.add_subparsers(dest="notes_command", required=True)

    p = nsub.add_parser("add", help="CREATE")
    p.add_argument("title")
    p.add_argument("--content")
    p.add_argument("--author")
    p.add_argument("--tags", help="comma separated")
    p.add_argument("--pin", action="store_true")
    p.set_defaults(func=cmd_notes_add)

    p = nsub.add_parser("list", help="READ")
    p.add_argument("--author")
    p.add_argument("--search")
    p.add_argument("--pinned", action="store_true", help="pinned notes only")
    p.add_argument("--limit", type=int, default=50)
    p.set_defaults(func=cmd_notes_list)

    p = nsub.add_parser("show", help="READ one")
    p.add_argument("reference", help="id or part of the title")
    p.set_defaults(func=cmd_notes_show)

    p = nsub.add_parser("update", help="UPDATE")
    p.add_argument("reference")
    p.add_argument("--title")
    p.add_argument("--content")
    p.add_argument("--tags", help="comma separated")
    p.add_argument("--pin", action="store_true")
    p.add_argument("--unpin", action="store_true")
    p.set_defaults(func=cmd_notes_update)

    p = nsub.add_parser("delete", help="DELETE")
    p.add_argument("reference")
    p.add_argument("--yes", action="store_true")
    p.set_defaults(func=cmd_notes_delete)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])

    if not getattr(args, "func", None):
        parser.print_help()
        return 0

    try:
        args.func(args)
        return 0
    except ConfigError as exc:
        console.print(Panel(str(exc), title="Configuration problem", border_style="red"))
        return 2
    except FileNotFoundError as exc:
        console.print(f"[red]{exc}[/]")
        return 3
    except KeyboardInterrupt:
        console.print("\nCancelled.")
        return 130
    except Exception as exc:  # noqa: BLE001
        console.print(Panel(f"{type(exc).__name__}: {exc}", title="Error", border_style="red"))
        return 1
