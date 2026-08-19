-- ============================================================================
--  Depot — one-time database setup
-- ----------------------------------------------------------------------------
--  HOW TO RUN THIS
--    1. Open https://app.supabase.com  ->  your project
--    2. Left sidebar  ->  "SQL Editor"  ->  "New query"
--    3. Paste this whole file  ->  press "Run"
--
--  It is safe to run more than once (everything is "if not exists" / "on conflict").
--
--  WHAT IT BUILDS
--    * table  public.file_metadata  -> one row per uploaded file
--    * table  public.notes          -> a plain table, for the "table CRUD" demo
--    * bucket documents             -> where the actual file bytes live
--    * RLS policies                 -> permission rules so the app can read/write
-- ============================================================================


-- ---------------------------------------------------------------------------
-- 0. Extension used for gen_random_uuid()
-- ---------------------------------------------------------------------------
create extension if not exists pgcrypto;


-- ---------------------------------------------------------------------------
-- 1. file_metadata  — the "database half" of a file
--
--    Supabase Storage holds the BYTES. Postgres holds the FACTS about the file.
--    Keeping them in two places is the normal pattern: you can search, filter
--    and join metadata with plain SQL without ever touching the file itself.
-- ---------------------------------------------------------------------------
create table if not exists public.file_metadata (
    id               uuid primary key default gen_random_uuid(),

    -- where the bytes live in Storage
    bucket           text        not null default 'documents',
    object_path      text        not null unique,   -- e.g. 'alice/1723987654-report.pdf'

    -- what the client knew at upload time
    file_name        text        not null,
    mime_type        text,
    size_bytes       bigint,
    owner            text        not null default 'anonymous',
    tags             text[]      not null default '{}',

    -- ---- everything below is filled in by the EDGE FUNCTION, not the client ----
    category         text,           -- 'document' | 'image' | 'data' | 'archive' | 'other'
    size_human       text,           -- '1.4 MB'
    checksum_sha256  text,           -- proves the stored bytes are what we think
    validated        boolean     not null default false,
    validation       jsonb,          -- full server-side verdict, kept for auditing

    created_at       timestamptz not null default now(),
    updated_at       timestamptz not null default now()
);

create index if not exists file_metadata_owner_idx      on public.file_metadata (owner);
create index if not exists file_metadata_created_at_idx on public.file_metadata (created_at desc);


-- ---------------------------------------------------------------------------
-- 2. notes — a second, file-free table so you can practise plain table CRUD
-- ---------------------------------------------------------------------------
create table if not exists public.notes (
    id         uuid primary key default gen_random_uuid(),
    title      text        not null,
    content    text        not null default '',
    author     text        not null default 'anonymous',
    tags       text[]      not null default '{}',
    is_pinned  boolean     not null default false,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create index if not exists notes_author_idx on public.notes (author);


-- ---------------------------------------------------------------------------
-- 3. Keep updated_at honest, automatically, on every UPDATE
-- ---------------------------------------------------------------------------
create or replace function public.touch_updated_at()
returns trigger
language plpgsql
as $$
begin
    new.updated_at = now();
    return new;
end;
$$;

drop trigger if exists file_metadata_touch on public.file_metadata;
create trigger file_metadata_touch
    before update on public.file_metadata
    for each row execute function public.touch_updated_at();

drop trigger if exists notes_touch on public.notes;
create trigger notes_touch
    before update on public.notes
    for each row execute function public.touch_updated_at();


-- ---------------------------------------------------------------------------
-- 4. The Storage bucket that holds the actual file bytes
--    public = false  ->  files are NOT reachable by guessing the URL.
--    To share one you generate a short-lived "signed URL" (the app does this).
-- ---------------------------------------------------------------------------
insert into storage.buckets (id, name, public)
values ('documents', 'documents', false)
on conflict (id) do nothing;


-- ---------------------------------------------------------------------------
-- 5. Row Level Security (RLS)
--
--    Supabase exposes your tables over a public REST API. RLS is the wall that
--    decides who may see or change each ROW. With RLS on and no policy, nobody
--    (using the anon key) can do anything — that is the safe default.
--
--    Below we add DEMO policies: the anon key may do everything on these two
--    demo tables and on the 'documents' bucket. That keeps the learning app
--    simple. See the "Making this production-safe" section of the README for
--    how to swap these for real per-user rules.
-- ---------------------------------------------------------------------------
alter table public.file_metadata enable row level security;
alter table public.notes         enable row level security;

drop policy if exists "demo full access to file_metadata" on public.file_metadata;
create policy "demo full access to file_metadata"
    on public.file_metadata
    for all
    to anon, authenticated
    using (true)
    with check (true);

drop policy if exists "demo full access to notes" on public.notes;
create policy "demo full access to notes"
    on public.notes
    for all
    to anon, authenticated
    using (true)
    with check (true);

-- Storage objects are just rows in storage.objects, so they need policies too.
drop policy if exists "demo full access to documents bucket" on storage.objects;
create policy "demo full access to documents bucket"
    on storage.objects
    for all
    to anon, authenticated
    using (bucket_id = 'documents')
    with check (bucket_id = 'documents');


-- ---------------------------------------------------------------------------
-- Done. Quick sanity check:
-- ---------------------------------------------------------------------------
select 'file_metadata' as object, count(*) as rows from public.file_metadata
union all
select 'notes',                   count(*)          from public.notes
union all
select 'documents bucket',        count(*)          from storage.buckets where id = 'documents';
