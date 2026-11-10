(() => {
  const viewNames = new Set(["welcome", "guestbook"]);

  const getViewFromHash = () => {
    const name = window.location.hash.slice(1);
    return viewNames.has(name) ? name : "welcome";
  };

  const init = () => {
    const views = document.querySelectorAll("[data-view]");
    const links = document.querySelectorAll("[data-view-link]");

    const showView = (name, { updateHistory = false, moveFocus = false } = {}) => {
      if (!viewNames.has(name)) return;

      views.forEach((view) => {
        const active = view.dataset.view === name;
        view.hidden = !active;
        view.classList.toggle("is-active", active);
        if (active) view.setAttribute("tabindex", "-1");
      });

      links.forEach((link) => {
        if (link.dataset.viewLink === name) link.setAttribute("aria-current", "page");
        else link.removeAttribute("aria-current");
      });

      if (updateHistory) {
        const nextHash = name === "welcome" ? "#welcome" : `#${name}`;
        if (window.location.hash !== nextHash) window.history.pushState({ view: name }, "", nextHash);
      }

      if (moveFocus) {
        document.querySelector(`[data-view="${name}"]`)?.focus({ preventScroll: true });
        const behavior = window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth";
        window.scrollTo({ top: 0, behavior });
      }
    };

    links.forEach((link) => {
      link.addEventListener("click", (event) => {
        const name = link.dataset.viewLink;
        if (!viewNames.has(name)) return;
        event.preventDefault();
        showView(name, { updateHistory: true, moveFocus: true });
      });
    });

    window.addEventListener("popstate", () => showView(getViewFromHash()));
    window.addEventListener("hashchange", () => showView(getViewFromHash()));
    showView(getViewFromHash());
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init, { once: true });
  } else {
    init();
  }
})();
