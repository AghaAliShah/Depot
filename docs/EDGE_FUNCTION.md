# The Edge Function: `file-guard`

Source: [`supabase/functions/file-guard/index.ts`](../supabase/functions/file-guard/index.ts)

---

## 1. What an Edge Function actually is

A file of TypeScript that Supabase runs **on their servers**, on the
[Deno](https://deno.com) runtime, close to your users. You do not manage a
server, a container, or a port. Once deployed it is simply a URL:

```
POST https://<your-project>.supabase.co/functions/v1/file-guard
```

Your Python app calls it with `httpx.post(...)` — see [`app/edge.py`](../app/edge.py).

---

## 2. Why this logic cannot live in Python

The Python script runs **on the user's machine**. A user can edit it. If the
10 MB limit were a Python `if`, anyone could delete that line and upload a 2 GB
file. Anything you actually want enforced has to run somewhere the user cannot
edit.

The function also does two things the client is structurally unable to do
honestly:

- **It reads the bytes back out of Storage.** So it validates what *actually
  landed in the bucket*, not what the client *claims* it sent.
- **It uses the `service_role` key.** That key bypasses Row Level Security, so
  it can write the `validated: true` flag that the client is not trusted to set.
  The key is injected by Supabase as an environment variable and never leaves
  the server.

---

## 3. What it does, step by step

| # | Step | Why it matters |
|---|---|---|
| 1 | Reads `{bucket, object_path, owner, mime_type, tags}` from the JSON body | the client says *what* to check |
| 2 | Downloads that object from Storage with the service-role client | now it has the real bytes |
| 3 | **Validates**: not empty, ≤ 10 MB, extension on the allow-list | the actual gate |
| 4 | **Enriches**: SHA-256 checksum, `category`, human-readable size | metadata the client can't fake |
| 5 | If invalid → **deletes the object** and returns `422` + reasons | no junk is left in the bucket |
| 6 | If valid → **upserts** the row in `file_metadata`, returns it | one source of truth |

Step 6 uses `upsert(..., { onConflict: "object_path" })`. That is what makes
"replace this file" work through the same code path: same path → the existing
row is updated in place with the new checksum and size, and the file keeps its id.

Limits live at the top of the file:

```ts
const MAX_BYTES = 10 * 1024 * 1024;
const ALLOWED_EXTENSIONS = { ".txt": "document", ".pdf": "document", ... };
```

Change them, redeploy, and every client is instantly bound by the new rules.

---

## 4. Deploying it

### Option A — the Supabase CLI (recommended)

Install the CLI (pick one):

```bash
winget install Supabase.CLI
```

```bash
npm install -g supabase
```

```bash
scoop install supabase
```

Then, from the project root:

```bash
supabase login
```

```bash
supabase link --project-ref YOUR_PROJECT_REF
```

`YOUR_PROJECT_REF` is the random part of your project URL —
`https://`**`abcdefghijklm`**`.supabase.co`.

```bash
supabase functions deploy file-guard
```

The CLI finds `supabase/functions/file-guard/index.ts` by itself, because that
folder layout *is* the convention.

### Option B — no CLI, straight from the dashboard

1. Dashboard → **Edge Functions** → **Deploy a new function**.
2. Name it exactly `file-guard`.
3. Paste the entire contents of `supabase/functions/file-guard/index.ts`.
4. Deploy.

Same result. The CLI is nicer once you are iterating.

---

## 5. Testing it

### From the app

```bash
python main.py check
```

Prints `edge function file-guard ok (max 10 MB, 16 allowed extensions)`. That is
a plain `GET` on the function — proof it is deployed and your key is accepted.

### The happy path

```bash
python main.py files upload sample_files/hello.txt
```

The metadata panel it prints came *back from the function*: the checksum,
`category`, and `size_human` fields were computed on the server.

### The rejection path — this is the interesting one

```bash
python main.py files upload sample_files/blocked.exe
```

```
Edge Function rejected the file:
    - Extension ".exe" is not on the allow-list.
```

Now check the dashboard: **Storage → documents** has no `blocked.exe`, and
**Table Editor → file_metadata** has no row for it. The function deleted the
object it had just found. The client asked nicely; the server said no and
cleaned up.

### Locally, before deploying

```bash
supabase functions serve file-guard
```

Then point `.env` at it, or curl it directly:

```bash
curl -i --request GET http://localhost:54321/functions/v1/file-guard --header "Authorization: Bearer YOUR_ANON_KEY"
```

### Reading the logs

Dashboard → **Edge Functions** → `file-guard` → **Logs**. Every `console.log`
and every uncaught error shows up there. This is where you look when a call
returns 500.

---

## 6. `verify_jwt`

[`supabase/config.toml`](../supabase/config.toml) sets:

```toml
[functions.file-guard]
verify_jwt = true
```

That means callers must send `Authorization: Bearer <a valid Supabase JWT>`.
The anon key *is* a JWT, so `app/edge.py` just sends that and the check passes.
Set it to `false` only if you deliberately want an endpoint the whole internet
can call without a key.

---

## 7. Common failures

| Symptom | Likely cause |
|---|---|
| `404` from the function | not deployed, or the name in `.env` doesn't match |
| `401 Invalid JWT` | anon key wrong, truncated, or `Authorization` header missing |
| `404 Object not found in storage` | bucket name in `.env` ≠ bucket the function looks in |
| `500` with no detail | check the dashboard logs; usually a typo in the TypeScript |
| Upload hangs then fails | large file + slow link; the SDK is uploading before the function is even called |
