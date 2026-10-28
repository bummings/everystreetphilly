import { timingSafeEqual } from "node:crypto";
import { getStore } from "@netlify/blobs";

const store = getStore("guestbook", { consistency: "strong" });
const jsonHeaders = {
  "Content-Type": "application/json; charset=utf-8",
  "Cache-Control": "no-store",
  "X-Content-Type-Options": "nosniff",
};
const respond = (body, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: jsonHeaders });

function isAuthorized(request) {
  const expected = process.env.GUESTBOOK_ADMIN_TOKEN;
  const supplied = request.headers.get("authorization")?.replace(/^Bearer\s+/i, "") || "";
  if (!expected || expected.length < 32 || supplied.length !== expected.length) return false;
  return timingSafeEqual(Buffer.from(supplied), Buffer.from(expected));
}

async function findEntries() {
  const { blobs } = await store.list({ prefix: "entries/" });
  const entries = await Promise.all(blobs.map(({ key }) => store.get(key, { type: "json" })));
  return entries.filter(Boolean).sort((a, b) => b.createdAt.localeCompare(a.createdAt));
}

export default async (request) => {
  if (!isAuthorized(request)) return respond({ error: "Admin access denied." }, 401);

  try {
    if (request.method === "GET") {
      return respond({ entries: await findEntries() });
    }

    if (request.method !== "PATCH" && request.method !== "DELETE") {
      return respond({ error: "Method not allowed." }, 405);
    }

    let data;
    try {
      data = await request.json();
    } catch {
      return respond({ error: "Please send a valid request." }, 400);
    }
    if (!data || typeof data !== "object" || Array.isArray(data)) {
      return respond({ error: "Please send a valid request." }, 400);
    }

    const id = typeof data.id === "string" && /^[0-9a-f-]{36}$/i.test(data.id) ? data.id : "";
    if (!id) return respond({ error: "That entry could not be found." }, 400);
    const key = `entries/${id}`;
    const entry = await store.get(key, { type: "json" });
    if (!entry) return respond({ error: "That entry no longer exists." }, 404);

    if (request.method === "DELETE") {
      await store.delete(key);
      return respond({ ok: true });
    }

    if (data.action !== "approve" || entry.status !== "pending") {
      return respond({ error: "That action is not available for this entry." }, 400);
    }

    const approved = { ...entry, status: "approved", approvedAt: new Date().toISOString() };
    await store.setJSON(key, approved);
    return respond({ ok: true, entry: approved });
  } catch (error) {
    console.error("Guestbook moderation failed", error);
    return respond({ error: "Moderation is temporarily unavailable." }, 503);
  }
};
