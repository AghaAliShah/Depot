// ============================================================================
//  Edge Function: file-guard
// ----------------------------------------------------------------------------
//  WHAT IS AN EDGE FUNCTION?
//    A small piece of TypeScript that Supabase runs FOR you, on their servers,
//    on the Deno runtime. Your Python app calls it over plain HTTPS.
//
//  WHY NOT JUST DO THIS IN PYTHON?
//    Because anything running on the client can be lied to. A user could patch
//    the Python script and skip the size check. This function runs on the
//    server with the SERVICE ROLE key, so its verdict is the one that counts.
//
//  WHAT IT DOES, IN ORDER
//    1. Reads the object that was just uploaded, straight from Storage.
//    2. VALIDATES it   — real byte size, extension allow-list, not empty.
//    3. ENRICHES it    — SHA-256 checksum, category, human-readable size.
//    4. On failure     — DELETES the object again, so no junk is left behind,
//                        and returns 422 with the reasons.
//    5. On success     — writes/updates the row in public.file_metadata and
//                        returns that row.
//
//  Note that steps 1 and 4 are things the client literally cannot be trusted
//  to do: the function checks the bytes that ACTUALLY landed in the bucket,
//  not the bytes the client claims it sent.
// ============================================================================

import { createClient } from "https://esm.sh/@supabase/supabase-js@2";

// ---------------------------------------------------------------------------
// House rules. Change these and redeploy to change what the server accepts.
// ---------------------------------------------------------------------------
const MAX_BYTES = 10 * 1024 * 1024; // 10 MB

const ALLOWED_EXTENSIONS: Record<string, string> = {
  // extension -> category
  ".txt": "document",
  ".md": "document",
  ".pdf": "document",
  ".doc": "document",
  ".docx": "document",
  ".rtf": "document",
  ".csv": "data",
  ".json": "data",
  ".xml": "data",
  ".xlsx": "data",
  ".png": "image",
  ".jpg": "image",
  ".jpeg": "image",
  ".gif": "image",
  ".webp": "image",
  ".zip": "archive",
};

const CORS_HEADERS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers":
    "authorization, x-client-info, apikey, content-type",
  "Access-Control-Allow-Methods": "POST, OPTIONS",
};

// ---------------------------------------------------------------------------
// Small helpers
// ---------------------------------------------------------------------------
function extensionOf(name: string): string {
  const dot = name.lastIndexOf(".");
  return dot === -1 ? "" : name.slice(dot).toLowerCase();
}

function humanSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KB", "MB", "GB"];
  let value = bytes / 1024;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value.toFixed(1)} ${units[unit]}`;
}

async function sha256(bytes: Uint8Array): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return Array.from(new Uint8Array(digest))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body, null, 2), {
    status,
    headers: { ...CORS_HEADERS, "Content-Type": "application/json" },
  });
}

// ---------------------------------------------------------------------------
// The function itself
// ---------------------------------------------------------------------------
Deno.serve(async (req: Request) => {
  // Browsers send an OPTIONS "preflight" before a cross-origin POST.
  if (req.method === "OPTIONS") {
    return new Response("ok", { headers: CORS_HEADERS });
  }

  // A friendly GET so you can check the function is alive in a browser.
  if (req.method === "GET") {
    return json({
      function: "file-guard",
      status: "alive",
      max_bytes: MAX_BYTES,
      allowed_extensions: Object.keys(ALLOWED_EXTENSIONS),
    });
  }

  if (req.method !== "POST") {
    return json({ error: "Use POST." }, 405);
  }

  // -- 0. Read what the client is telling us ---------------------------------
  let payload: Record<string, unknown>;
  try {
    payload = await req.json();
  } catch {
    return json({ error: "Body must be JSON." }, 400);
  }

  const bucket = String(payload.bucket ?? "documents");
  const objectPath = String(payload.object_path ?? "");
  const owner = String(payload.owner ?? "anonymous");
  const declaredMime = payload.mime_type ? String(payload.mime_type) : null;
  const tags = Array.isArray(payload.tags) ? payload.tags.map(String) : [];

  if (!objectPath) {
    return json({ error: "object_path is required." }, 400);
  }

  // -- 1. Talk to Supabase as the SERVICE ROLE -------------------------------
  //    These two env vars are injected automatically for every Edge Function.
  //    The service role key bypasses RLS, which is exactly why this key must
  //    never leave the server.
  const admin = createClient(
    Deno.env.get("SUPABASE_URL")!,
    Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!,
  );

  // -- 2. Fetch the real bytes that landed in the bucket ---------------------
  const { data: blob, error: downloadError } = await admin.storage
    .from(bucket)
    .download(objectPath);

  if (downloadError || !blob) {
    return json(
      {
        error: "Object not found in storage.",
        bucket,
        object_path: objectPath,
        detail: downloadError?.message ?? null,
      },
      404,
    );
  }

  const bytes = new Uint8Array(await blob.arrayBuffer());
  const realSize = bytes.byteLength;

  // -- 3. Validate -----------------------------------------------------------
  const fileName = objectPath.split("/").pop() ?? objectPath;
  const ext = extensionOf(fileName);
  const problems: string[] = [];

  if (realSize === 0) {
    problems.push("File is empty (0 bytes).");
  }
  if (realSize > MAX_BYTES) {
    problems.push(
      `File is ${humanSize(realSize)}, limit is ${humanSize(MAX_BYTES)}.`,
    );
  }
  if (!ext) {
    problems.push("File has no extension, so its type cannot be verified.");
  } else if (!(ext in ALLOWED_EXTENSIONS)) {
    problems.push(`Extension "${ext}" is not on the allow-list.`);
  }

  // -- 4. Rejected? Undo the upload and say why ------------------------------
  if (problems.length > 0) {
    await admin.storage.from(bucket).remove([objectPath]); // clean up the junk
    return json(
      {
        accepted: false,
        problems,
        object_path: objectPath,
        note: "The object was deleted from storage so nothing invalid is kept.",
      },
      422,
    );
  }

  // -- 5. Accepted: generate the metadata the client could not be trusted with
  const checksum = await sha256(bytes);
  const category = ALLOWED_EXTENSIONS[ext];
  const mimeType = declaredMime ?? blob.type ?? "application/octet-stream";

  const row = {
    bucket,
    object_path: objectPath,
    file_name: fileName,
    mime_type: mimeType,
    size_bytes: realSize,
    owner,
    tags,
    category,
    size_human: humanSize(realSize),
    checksum_sha256: checksum,
    validated: true,
    validation: {
      checked_at: new Date().toISOString(),
      checked_by: "file-guard",
      max_bytes: MAX_BYTES,
      extension: ext,
      problems: [],
    },
  };

  // upsert = insert, or update if this object_path already has a row.
  // That makes "replace the file" work through exactly the same code path.
  const { data: saved, error: dbError } = await admin
    .from("file_metadata")
    .upsert(row, { onConflict: "object_path" })
    .select()
    .single();

  if (dbError) {
    return json({ accepted: false, error: dbError.message }, 500);
  }

  return json({ accepted: true, metadata: saved }, 200);
});
