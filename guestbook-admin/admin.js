const ADMIN_TOKEN_KEY = "every-street-philly-admin-token";
const API_URL = "/.netlify/functions/guestbook-admin";

window.addEventListener("DOMContentLoaded", () => {
  const login = document.querySelector("#admin-login");
  const tokenInput = document.querySelector("#admin-token");
  const toolbar = document.querySelector(".admin-toolbar");
  const status = document.querySelector("#admin-status");
  const entriesRoot = document.querySelector("#admin-entries");
  let token = sessionStorage.getItem(ADMIN_TOKEN_KEY) || "";

  const request = async (method = "GET", payload) => {
    const response = await fetch(API_URL, {
      method,
      headers: {
        Authorization: `Bearer ${token}`,
        ...(payload ? { "Content-Type": "application/json" } : {}),
      },
      ...(payload ? { body: JSON.stringify(payload) } : {}),
      cache: "no-store",
    });
    const result = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(result.error || "Could not complete that request.");
    return result;
  };

  const makeEntry = (entry) => {
    const article = document.createElement("article");
    article.className = "admin-entry";
    const header = document.createElement("header");
    const identity = document.createElement("strong");
    identity.textContent = entry.name;
    const date = document.createElement("time");
    date.dateTime = entry.createdAt;
    date.textContent = new Intl.DateTimeFormat("en-US", {
      dateStyle: "medium",
      timeStyle: "short",
    }).format(new Date(entry.createdAt));
    header.append(identity, date);
    const message = document.createElement("p");
    message.textContent = entry.message;
    const actions = document.createElement("div");
    actions.className = "admin-entry-actions";

    if (entry.status === "pending") {
      const approve = document.createElement("button");
      approve.type = "button";
      approve.textContent = "Approve and publish";
      approve.addEventListener("click", () => moderate("PATCH", { id: entry.id, action: "approve" }));
      actions.append(approve);
    } else {
      const state = document.createElement("span");
      state.className = "entry-state";
      state.textContent = "Published";
      actions.append(state);
    }

    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "delete-button";
    remove.textContent = "Delete";
    remove.addEventListener("click", () => {
      if (window.confirm(`Permanently delete the note from ${entry.name}?`)) {
        moderate("DELETE", { id: entry.id });
      }
    });
    actions.append(remove);
    article.append(header, message, actions);
    return article;
  };

  const renderGroup = (title, entries) => {
    const section = document.createElement("section");
    const heading = document.createElement("h2");
    heading.className = "admin-section-title";
    heading.textContent = `${title} (${entries.length})`;
    section.append(heading);
    if (!entries.length) {
      const empty = document.createElement("p");
      empty.className = "admin-empty";
      empty.textContent = title === "Needs review" ? "Nothing waiting for review." : "Nothing published yet.";
      section.append(empty);
    } else {
      entries.forEach((entry) => section.append(makeEntry(entry)));
    }
    return section;
  };

  const loadInbox = async () => {
    status.textContent = "Loading guestbook…";
    try {
      const { entries } = await request();
      entriesRoot.replaceChildren(
        renderGroup("Needs review", entries.filter((entry) => entry.status === "pending")),
        renderGroup("Published", entries.filter((entry) => entry.status === "approved")),
      );
      toolbar.hidden = false;
      login.hidden = true;
      status.textContent = "Your moderation changes save immediately.";
    } catch (error) {
      sessionStorage.removeItem(ADMIN_TOKEN_KEY);
      token = "";
      login.hidden = false;
      toolbar.hidden = true;
      entriesRoot.replaceChildren();
      status.textContent = error.message;
      tokenInput.focus();
    }
  };

  const moderate = async (method, payload) => {
    status.textContent = "Saving…";
    try {
      await request(method, payload);
      await loadInbox();
    } catch (error) {
      status.textContent = error.message;
    }
  };

  login.addEventListener("submit", async (event) => {
    event.preventDefault();
    token = tokenInput.value.trim();
    sessionStorage.setItem(ADMIN_TOKEN_KEY, token);
    await loadInbox();
    tokenInput.value = "";
  });
  document.querySelector("#admin-refresh").addEventListener("click", loadInbox);
  document.querySelector("#admin-sign-out").addEventListener("click", () => {
    sessionStorage.removeItem(ADMIN_TOKEN_KEY);
    token = "";
    entriesRoot.replaceChildren();
    login.hidden = false;
    toolbar.hidden = true;
    tokenInput.focus();
  });

  if (token) loadInbox();
});
