# Depot — files, tables, and a server-side guard on Supabase

A terminal app (plus an optional web UI) that does full **C**reate / **R**ead /
**U**pdate / **D**elete against Supabase:

- **Files** → stored in Supabase **Storage**, described by a row in **Postgres**,
  and policed by an **Edge Function** that runs on Supabase's servers.
- **Notes** → a plain table, to show table CRUD on its own without any file stuff.

Everything is written to be read. If you have never touched Supabase, start at
[Part 1](#part-1--the-mental-model).

---

## Part 1 — the mental model

### What Supabase gives you

Supabase is a hosted Postgres database with a set of useful things bolted on.
This project uses three of them:

| Piece | What it holds | How you talk to it here |
|---|---|---|
| **Database** (Postgres) | rows — structured facts | `client.table("notes")` |
| **Storage** | files — the raw bytes | `client.storage.from_("documents")` |
| **Edge Functions** | your code, running on their servers | an HTTPS `POST` |

The important idea: **Supabase auto-generates a REST API over your database.**
You do not write a backend. The Python SDK is a thin wrapper over that API.

Which raises an obvious question — if the database is on the public internet,
what stops anyone from reading it? That is what RLS is for; see
[Part 6](#part-6--security-rls-and-keys).

### The one idea that makes file storage click

**A file lives in two places at once.**

```
   the BYTES                            the FACTS
   ---------                            ---------
   Supabase Storage                     Postgres table `file_metadata`
   bucket: documents                    id, file_name, size, owner, tags,
   key:    demo_user/1700-report.pdf    category, checksum, created_at
        |                                        |
        +---------- object_path links them ------+
```

Why split them? Because you cannot run `SELECT ... WHERE size > 1MB AND
tag = 'invoice'` over a pile of blobs. Storage is good at holding bytes and bad
at answering questions; Postgres is the opposite. So you keep the bytes in
Storage and everything you might want to *search* in a table, joined by the
`object_path`.

Every operation in this app is really "do the Storage half and the Postgres half,
in the right order".

### Why an Edge Function is in the middle

Your Python script runs **on your laptop**. Anyone can open it and delete the
line that checks the file size. So any rule you actually want *enforced* has to
run somewhere the user cannot edit — on the server.

`file-guard` is that server. After the upload it **re-reads the bytes out of the
bucket** and judges those, not whatever the client claimed. If it doesn't like
them it deletes the object and returns an error.

```
  Python (untrusted)                 Supabase (trusted)
  ------------------                 ------------------
  1. upload bytes  ---------------->  bucket `documents`
  2. POST /file-guard  ------------>  Edge Function
                                        |- downloads the object it just got
                                        |- checks size + extension
                                        |- computes SHA-256, category
                                        |
                          rejected <----+  deletes the object, 422 + reasons
                          accepted <----+  INSERT into file_metadata, returns row
  3. print the row  <---------------
```

Notice the client **never writes the metadata row**. It cannot mark a file
`validated`. That is the whole point.

---

## Part 2 — getting it running

Full walk-through with screenshots-worth-of-detail: **[docs/SETUP.md](docs/SETUP.md)**.
The four-line version:

1. Create a project at <https://app.supabase.com>.
2. `copy .env.example .env` and paste in your **Project URL** and **anon key**
   (Project Settings → API).
3. SQL Editor → paste [`sql/schema.sql`](sql/schema.sql) → Run. That creates both
   tables, the `documents` bucket, and the security policies.
4. Install and deploy:

```bash
python -m venv .venv
```

```bash
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

```bash
supabase login && supabase link --project-ref YOUR_REF && supabase functions deploy file-guard
```

Then confirm everything is wired up:

```bash
.venv\Scripts\python.exe main.py check
```

```
url                      https://xxxxxxxx.supabase.co
table file_metadata      ok
table notes              ok
bucket documents         ok
edge function file-guard ok (max 10 MB, 16 allowed extensions)
```

No Supabase CLI? [docs/EDGE_FUNCTION.md §4](docs/EDGE_FUNCTION.md#4-deploying-it)
shows how to paste the function straight into the dashboard instead.

---

## Part 3 — using it

Two front ends, both calling the exact same functions.

### Interactive menu (start here)

```bash
python main.py menu
```

A numbered menu — upload, list, download, share, replace, delete, plus the notes.
Nothing to memorise.

### One-shot commands

**Files**

```bash
python main.py files upload sample_files/hello.txt --tags demo,intro
```

```bash
python main.py files list
```

```bash
python main.py files show hello
```

```bash
python main.py files download hello --to downloads
```

```bash
python main.py files link hello --expires 600
```

```bash
python main.py files update hello --tags invoice,2026
```

```bash
python main.py files replace hello sample_files/people.csv
```

```bash
python main.py files delete hello
```

`hello` there is a **reference** — you can pass a full id, the exact object path,
or just part of the file name. If more than one file matches, the app lists the
candidates instead of guessing.

**Notes** (plain table CRUD, no files)

```bash
python main.py notes add "Read the RLS docs" --content "before shipping" --tags todo --pin
```

```bash
python main.py notes list --search rls
```

```bash
python main.py notes update "Read the RLS" --content "done" --unpin
```

```bash
python main.py notes delete "Read the RLS"
```

### Web UI (optional)

```bash
streamlit run ui/streamlit_app.py
```

Same operations with buttons. It imports `app/storage_crud.py` and
`app/table_crud.py` — no Supabase logic is duplicated in the UI.

### See the guard actually refuse something

```bash
python main.py files upload sample_files/blocked.exe
```

```
Edge Function rejected the file:
    - Extension ".exe" is not on the allow-list.
```

Now look in the dashboard: **Storage → documents** has no `blocked.exe`, and
`file_metadata` has no row for it. The function deleted the object it had just
found. That cleanup is why nothing invalid ever accumulates.

---

## Part 4 — what each file does

```
SupabaseCRUD/
│
├── main.py                    entry point -> app/cli.py
├── .env                       your keys (git-ignored; copy from .env.example)
│
├── app/
│   ├── config.py              reads .env, fails with a fix-it message
│   ├── supabase_client.py     builds the one shared client + `ping()` health check
│   ├── storage_crud.py        FILE CRUD  (Storage bytes + metadata row)
│   ├── table_crud.py          NOTE CRUD  (plain table — read this one first)
│   ├── edge.py                calls the Edge Function over HTTPS
│   └── cli.py                 argparse commands + the interactive menu
│
├── supabase/
│   ├── config.toml            CLI project config (verify_jwt setting)
│   └── functions/file-guard/
│       └── index.ts           THE EDGE FUNCTION — validate, enrich, save, clean up
│
├── sql/schema.sql             run once: tables, trigger, bucket, RLS policies
├── ui/streamlit_app.py        optional web UI
├── tests/test_offline.py      13 tests that run with no project and no network
├── sample_files/              hello.txt, people.csv, blocked.exe (for testing)
└── docs/
    ├── SETUP.md               step-by-step first-time setup + troubleshooting
    └── EDGE_FUNCTION.md       deep dive: what it does, deploy, test, debug
```

**If you are reading the code for the first time**, go in this order:
`app/table_crud.py` (simplest — just SQL) → `app/storage_crud.py` (adds files) →
`supabase/functions/file-guard/index.ts` (adds the server).

---

## Part 5 — the four operations, precisely

### CREATE — `files upload`

```
create_file()
  1. read the local file
  2. build object_path = "<owner>/<timestamp>-<slugified-name>"   <- never collides
  3. storage.upload(object_path, bytes)
  4. POST /functions/v1/file-guard  { bucket, object_path, owner, mime_type, tags }
       the function validates, enriches, and INSERTs the row itself
  5. if step 4 failed for any reason -> remove the object, so nothing is orphaned
```

The timestamp prefix matters: two people uploading `report.pdf` get two distinct
objects instead of silently overwriting each other.

### READ — `files list` / `show` / `download` / `link`

- `list` and `show` are **pure Postgres**. Listing 500 files touches zero bytes.
- `download` is the only one that pulls the object out of Storage.
- `link` creates a **signed URL** — a temporary link to a file in a *private*
  bucket. After `--expires` seconds it stops working. This is how you share a
  file without making the bucket public.

### UPDATE — two different things

| Command | Changes | Edge Function runs? |
|---|---|---|
| `files update` | the row only (name, owner, tags) | no — no new bytes to check |
| `files replace` | the bytes, at the same `object_path` | **yes** — re-validated from scratch |

`replace` works because the function does an **upsert on `object_path`**: same
path → the existing row is updated in place with the new size and checksum, and
the file keeps its id. The `updated_at` column is bumped by a database trigger,
not by the app.

### DELETE — `files delete`

Bytes first, **then** the row. If it were the other way round and the storage
call failed, you would be left with a file nobody can find or name — an orphan.
This order can only ever fail the safe way (a row pointing at nothing, which is
easy to spot and clean).

---

## Part 6 — security: RLS and keys

### The three keys

| Key | Where it belongs | What it can do |
|---|---|---|
| `anon` | your `.env`, the browser, anywhere | only what RLS policies allow |
| `service_role` | **servers only** — the Edge Function gets it automatically | ignores RLS entirely |
| database password | nowhere in this app | direct psql access |

The `service_role` key is deliberately **not** in `.env.example`. If it ever
reaches a client, everything you wrote in Part 6 stops being true.

### Row Level Security

Enabling RLS on a table means "deny everything unless a policy says otherwise".
`sql/schema.sql` then adds **demo** policies that let the anon key do anything
on these two demo tables and the `documents` bucket, which is what keeps this
learning project simple.

**That is not what you would ship.** In a real app you would add Supabase Auth
and write policies against the logged-in user, for example:

```sql
alter table public.file_metadata enable row level security;

create policy "users see only their own files"
    on public.file_metadata
    for select
    to authenticated
    using (auth.uid() = user_id);       -- with owner changed to a uuid column
```

`auth.uid()` is the id of whoever is making the request, taken from their JWT.
Postgres enforces it on every single query — you cannot forget to add the filter,
because the filter is the table's own rule.

Other things worth doing before this goes anywhere real:

- swap `owner text` for `user_id uuid references auth.users(id)`
- keep the `documents` bucket private (it already is) and share via signed URLs
- add a storage policy that only lets a user touch objects under their own prefix
- tighten `MAX_BYTES` and the extension allow-list in the Edge Function

---

## Part 7 — checking your changes

```bash
.venv\Scripts\python.exe tests/test_offline.py
```

13 tests, no network, no Supabase project needed — they swap in a fake client
and assert on what the app *asked it to do*: that upload happens before
validation, that a failed validation removes the object, that delete removes
bytes before the row, that an ambiguous name is reported rather than guessed.

```
Ran 13 tests in 0.4s

OK
```

Run them after editing anything in `app/`. To test against the real project,
use `python main.py check` and then the sample files in `sample_files/`.

---

## Reference

| Command | Does |
|---|---|
| `python main.py check` | verify .env, both tables, the bucket, the function |
| `python main.py menu` | interactive menu |
| `python main.py files upload PATH [--tags a,b] [--owner x]` | CREATE |
| `python main.py files list [--search s] [--owner o] [--limit n]` | READ many |
| `python main.py files show REF` | READ one |
| `python main.py files download REF [--to DIR]` | READ bytes |
| `python main.py files link REF [--expires N]` | temporary share URL |
| `python main.py files update REF [--name] [--owner] [--tags]` | UPDATE row |
| `python main.py files replace REF PATH` | UPDATE bytes + re-validate |
| `python main.py files delete REF [--yes]` | DELETE both |
| `python main.py notes add TITLE [--content] [--tags] [--pin]` | CREATE |
| `python main.py notes list [--search] [--author] [--pinned]` | READ |
| `python main.py notes show REF` | READ one |
| `python main.py notes update REF [--title] [--content] [--tags] [--pin/--unpin]` | UPDATE |
| `python main.py notes delete REF [--yes]` | DELETE |
| `streamlit run ui/streamlit_app.py` | web UI |

Environment variables (`.env`): `SUPABASE_URL`, `SUPABASE_ANON_KEY` are required;
`SUPABASE_BUCKET`, `EDGE_FUNCTION_NAME`, `APP_USER`, `DOWNLOAD_DIR` are optional.

Further reading: [Supabase Python SDK](https://supabase.com/docs/reference/python/introduction)
· [Storage](https://supabase.com/docs/guides/storage)
· [Edge Functions](https://supabase.com/docs/guides/functions)
· [Row Level Security](https://supabase.com/docs/guides/database/postgres/row-level-security)
