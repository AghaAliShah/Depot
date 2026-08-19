# Setup — from zero to a working app

Follow this once. Should take about 10 minutes.

---

## 1. Create the Supabase project

1. Go to <https://app.supabase.com> and sign in.
2. **New project** → give it a name, pick a region near you, set a database password
   (you will not need it for this app, but save it anyway).
3. Wait for the green "Project is ready".

Database and Storage are on by default — there is nothing to "enable".

---

## 2. Copy your keys

**Project Settings → API**. You need exactly two values:

| Field in the dashboard | Goes into `.env` as | What it is |
|---|---|---|
| Project URL | `SUPABASE_URL` | `https://xxxxxxxx.supabase.co` |
| `anon` / `public` key | `SUPABASE_ANON_KEY` | a long JWT, safe for client apps |

> There is a third key, `service_role`. **Do not put it in `.env`.** It ignores all
> security rules. The Edge Function receives it automatically from Supabase; nothing
> on your laptop needs it.

Now create your env file:

```bash
copy .env.example .env
```

and paste the two values in.

---

## 3. Create the tables and the bucket

1. Dashboard → **SQL Editor** → **New query**.
2. Open [`sql/schema.sql`](../sql/schema.sql), copy the whole thing, paste, **Run**.

You should see a small result table at the bottom listing `file_metadata`,
`notes` and `documents bucket`. That script creates:

- table `file_metadata` — one row per uploaded file
- table `notes` — for the plain-table CRUD demo
- bucket `documents` — private, holds the actual bytes
- Row Level Security policies so the anon key is allowed to use all three

Check it visually: **Table Editor** should now show both tables, and **Storage**
should show a `documents` bucket.

---

## 4. Install the Python side

```bash
python -m venv .venv
```

```bash
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

---

## 5. Deploy the Edge Function

Full detail (and a no-CLI fallback) is in [EDGE_FUNCTION.md](EDGE_FUNCTION.md).
The short version:

```bash
supabase login
```

```bash
supabase link --project-ref YOUR_PROJECT_REF
```

```bash
supabase functions deploy file-guard
```

---

## 6. Prove it works

```bash
.venv\Scripts\python.exe main.py check
```

Every line should say `ok`:

```
url                    https://xxxxxxxx.supabase.co
table file_metadata    ok
table notes            ok
bucket documents       ok
edge function file-guard ok (max 10 MB, 16 allowed extensions)
```

Then upload something:

```bash
.venv\Scripts\python.exe main.py files upload sample_files/hello.txt
```

---

## When a step goes wrong

| Message | Cause | Fix |
|---|---|---|
| `SUPABASE_URL is missing` | no `.env`, or it is empty | step 2 |
| `relation "public.file_metadata" does not exist` | schema not run | step 3 |
| `new row violates row-level security policy` | RLS on, policies missing | re-run `sql/schema.sql`; it creates the policies |
| `Bucket not found` | bucket missing or renamed | re-run `sql/schema.sql`, or fix `SUPABASE_BUCKET` in `.env` |
| `edge function: FAILED ... 404` | function not deployed | step 5 |
| `Invalid JWT` on the function | wrong / truncated anon key | re-copy it from the dashboard |
| `Object not found in storage` from the function | bucket name mismatch between `.env` and the function's default | make both say `documents` |
