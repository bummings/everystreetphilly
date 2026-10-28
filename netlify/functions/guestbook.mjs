import { createHmac, randomUUID } from "node:crypto";
import { getStore } from "@netlify/blobs";

const store = getStore("guestbook", { consistency: "strong" });
const jsonHeaders = {
  "Content-Type": "application/json; charset=utf-8",
  "Cache-Control": "no-store",
  "X-Content-Type-Options": "nosniff",
};

const respond = (body, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: jsonHeaders });

const cleanText = (value, maxLength) =>
  typeof value === "string"
    ? value.replace(/[\u0000-\u0008\u000B\u000C\u000E-\u001F\u007F]/g, "").trim().slice(0, maxLength)
    : "";

async function listEntries() {
  const { blobs } = await store.list({ prefix: "entries/" });
  const values = await Promise.all(
    blobs.map(({ key }) => store.get(key, { type: "json" })),
  );
  return values.filter(Boolean).sort((a, b) => b.createdAt.localeCompare(a.createdAt));
}

export default async (request, context) => {
  if (request.method === "GET") {
    try {
      const entries = (await listEntries())
        .filter((entry) => entry.status === "approved")
        .map(({ id, name, message, createdAt }) => ({ id, name, message, createdAt }));
      return respond({ entries });
    } catch (error) {
      console.error("Guestbook read failed", error);
      return respond({ error: "The guestbook is temporarily unavailable." }, 503);
    }
  }

  if (request.method !== "POST") {
    return respond({ error: "Method not allowed." }, 405);
  }

  if ((process.env.GUESTBOOK_ADMIN_TOKEN || "").length < 32) {
    return respond({ error: "The guestbook is still being set up. Please try again later." }, 503);
  }

  const contentLength = Number(request.headers.get("content-length") || 0);
  if (contentLength > 10_000) return respond({ error: "That note is too large." }, 413);

  let data;
  try {
    data = await request.json();
  } catch {
    return respond({ error: "Please send a valid note." }, 400);
  }
  if (!data || typeof data !== "object" || Array.isArray(data)) {
    return respond({ error: "Please send a valid note." }, 400);
  }

  // Quietly discard honeypot submissions so simple bots don't learn the trap.
  if (cleanText(data.website, 200)) {
    return respond({ message: "Thanks! Your note is in the review queue." }, 202);
  }

  const name = cleanText(data.name, 40);
  const message = cleanText(data.message, 500);
  const startedAt = Number(data.startedAt);
  if (!name || !message) return respond({ error: "Please add your name and a note." }, 400);
  if (!Number.isFinite(startedAt) || Date.now() - startedAt < 1800) {
    return respond({ error: "Please take a moment to review your note, then try again." }, 400);
  }

  try {
    // Netlify supplies context.ip. Hash it before using it as a short-lived rate-limit key.
    if (context.ip) {
      const ipHash = createHmac("sha256", process.env.GUESTBOOK_ADMIN_TOKEN).update(context.ip).digest("hex");
      const rateKey = `rate/${ipHash}`;
      const previous = await store.get(rateKey, { type: "json" });
      if (previous && Date.now() - previous.at < 20_000) {
        return respond({ error: "Please wait a few seconds before signing again." }, 429);
      }
      await store.setJSON(rateKey, { at: Date.now() });
    }

    const entry = {
      id: randomUUID(),
      name,
      message,
      createdAt: new Date().toISOString(),
      status: "pending",
    };
    await store.setJSON(`entries/${entry.id}`, entry, { onlyIfNew: true });
    return respond({ message: "Thanks! Your note is in the review queue." }, 202);
  } catch (error) {
    console.error("Guestbook submission failed", error);
    return respond({ error: "Could not save your note just now. Please try again later." }, 503);
  }
};
