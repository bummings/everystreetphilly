const STORAGE_KEYS = {
  entries: "every-street-philly-guestbook-entries",
  visits: "every-street-philly-visit-count",
};

const getStoredEntries = () => {
  try {
    return JSON.parse(localStorage.getItem(STORAGE_KEYS.entries)) ?? [];
  } catch {
    return [];
  }
};

const saveEntries = (entries) => {
  localStorage.setItem(STORAGE_KEYS.entries, JSON.stringify(entries));
};

const initializeRunMap = async () => {
  const status = document.querySelector("#map-status");

  if (!status) return;

  if (!window.maplibregl) {
    status.textContent = "The interactive map needs an internet connection to load.";
    return;
  }

  const map = new maplibregl.Map({
    container: "run-map",
    center: [-75.1652, 39.9526],
    zoom: 10.6,
    minZoom: 9.5,
    maxZoom: 18,
    style: {
      version: 8,
      sources: {
        "open-street-map": {
          type: "raster",
          tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"],
          tileSize: 256,
          attribution: "© OpenStreetMap contributors",
        },
      },
      layers: [
        {
          id: "warm-paper",
          type: "background",
          paint: { "background-color": "#fff8ef" },
        },
        {
          id: "open-street-map",
          type: "raster",
          source: "open-street-map",
          paint: {
            "raster-opacity": 0.16,
            "raster-saturation": -1,
            "raster-contrast": -0.2,
            "raster-brightness-min": 0.78,
            "raster-brightness-max": 1,
          },
        },
      ],
    },
  });

  map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
  map.addControl(new maplibregl.ScaleControl({ maxWidth: 120, unit: "imperial" }), "bottom-left");

  map.on("load", () => {
    try {
      const routes = window.EVERY_STREET_ROUTES;
      const boundary = window.EVERY_STREET_BOUNDARY;
      const scorecard = window.EVERY_STREET_SCORECARD_ENABLED ? window.EVERY_STREET_SCORECARD : null;
      if (!routes || !boundary) throw new Error("Map data could not load.");
      if (scorecard) {
        const legend = document.querySelector(".map-legend");
        const caption = document.querySelector(".map-caption");
        const cardNote = document.querySelector(".card-small");
        if (legend) {
          legend.replaceChildren();
          [["legend-line--run", "completed street / recorded run"], ["legend-line--street", "not yet completed"]].forEach(([className, label]) => {
            const item = document.createElement("span");
            const line = document.createElement("i");
            line.className = `legend-line ${className}`;
            item.append(line, document.createTextNode(label));
            legend.append(item);
          });
        }
        if (caption) caption.textContent = "Scorecard preview: black shows verified street coverage plus the original GPS traces, so completed public trails such as Forbidden Drive remain visible. Coral means not yet verified as completed.";
        if (cardNote) cardNote.textContent = "Black is verified coverage or a recorded run. Coral is still waiting.";
      }

      const cityOutline = boundary.features[0].geometry.coordinates[0];
      const mask = {
        type: "FeatureCollection",
        features: [
          {
            type: "Feature",
            properties: {},
            geometry: {
              type: "Polygon",
              coordinates: [
                [
                  [-180, -85],
                  [180, -85],
                  [180, 85],
                  [-180, 85],
                  [-180, -85],
                ],
                cityOutline,
              ],
            },
          },
        ],
      };

      map.addSource("city-mask", { type: "geojson", data: mask });
      map.addSource("city-boundary", { type: "geojson", data: boundary });
      map.addSource("recorded-routes", { type: "geojson", data: routes });
      map.addLayer({
        id: "inside-city-wash",
        type: "fill",
        source: "city-boundary",
        paint: {
          "fill-color": "#eab19f",
          "fill-opacity": 0.26,
        },
      });
      if (scorecard) {
        map.addSource("remaining-streets", { type: "geojson", data: scorecard.remaining, attribution: "© OpenStreetMap contributors" });
        map.addSource("completed-streets", { type: "geojson", data: scorecard.completed, attribution: "© OpenStreetMap contributors" });
        map.addLayer({
          id: "remaining-streets-casing",
          type: "line",
          source: "remaining-streets",
          layout: { "line-cap": "round", "line-join": "round" },
          paint: {
            "line-color": "#fff8ef",
            "line-width": ["interpolate", ["linear"], ["zoom"], 10, 1.9, 13, 3.2, 16, 5.8, 18, 8],
          },
        });
        map.addLayer({
          id: "remaining-streets",
          type: "line",
          source: "remaining-streets",
          layout: { "line-cap": "round", "line-join": "round" },
          paint: {
            "line-color": "#f05b43",
            "line-width": ["interpolate", ["linear"], ["zoom"], 10, 1.15, 13, 2.1, 16, 4.1, 18, 6.2],
          },
        });
      }
      map.addLayer({
        id: "outside-city",
        type: "fill",
        source: "city-mask",
        paint: {
          "fill-color": "#241a17",
          "fill-opacity": 0.9,
        },
      });
      if (scorecard) {
        map.addLayer({
          id: "completed-streets",
          type: "line",
          source: "completed-streets",
          layout: { "line-cap": "round", "line-join": "round" },
          paint: {
            "line-color": "#241a17",
            "line-width": ["interpolate", ["linear"], ["zoom"], 10, 1.45, 13, 2.7, 16, 4.9, 18, 7.2],
          },
        });
        map.addLayer({
          id: "recorded-routes-on-scorecard",
          type: "line",
          source: "recorded-routes",
          layout: { "line-cap": "round", "line-join": "round" },
          paint: {
            "line-color": "#241a17",
            "line-opacity": 0.88,
            "line-width": ["interpolate", ["linear"], ["zoom"], 10, 1.25, 13, 2.5, 16, 4.6, 18, 6.8],
          },
        });
      } else {
        map.addLayer({
          id: "recorded-routes-halo",
          type: "line",
          source: "recorded-routes",
          paint: {
            "line-color": "#eab19f",
            "line-opacity": 0.55,
            "line-width": ["interpolate", ["linear"], ["zoom"], 10, 2.4, 14, 4.8, 18, 8],
          },
        });
        map.addLayer({
          id: "recorded-routes",
          type: "line",
          source: "recorded-routes",
          paint: {
            "line-color": "#241a17",
            "line-opacity": 0.9,
            "line-width": ["interpolate", ["linear"], ["zoom"], 10, 1.2, 14, 2.6, 18, 4.6],
          },
        });
      }
      map.addLayer({
        id: "city-boundary-outline",
        type: "line",
        source: "city-boundary",
        paint: {
          "line-color": "#241a17",
          "line-opacity": 0.94,
          "line-width": ["interpolate", ["linear"], ["zoom"], 10, 2.8, 14, 5.5, 18, 9],
        },
      });

      map.fitBounds(
        [
          [-75.294969, 39.869633],
          [-74.958518, 40.137118],
        ],
        { padding: 42, duration: 0 },
      );
      status.remove();
    } catch (error) {
      status.textContent = "The route layer is unavailable right now. Please try again when you are online.";
    }
  });
};

window.addEventListener("DOMContentLoaded", () => {
  document.body.classList.add("is-ready");
  initializeRunMap();

  const form = document.querySelector("#guestbook-form");
  const entriesList = document.querySelector("#guestbook-entries");
  const status = document.querySelector("#guestbook-status");
  const captchaQuestion = document.querySelector("#captcha-question");
  const answer = document.querySelector("#guest-answer");
  const counter = document.querySelector("#visit-count");
  let captchaAnswer = 0;

  const refreshCaptcha = () => {
    const first = Math.floor(Math.random() * 7) + 2;
    const second = Math.floor(Math.random() * 8) + 1;
    captchaAnswer = first + second;
    captchaQuestion.textContent = `Quick check: ${first} + ${second} =`;
    answer.value = "";
  };

  const renderEntries = () => {
    const entries = getStoredEntries();
    entriesList.replaceChildren();

    if (!entries.length) {
      const empty = document.createElement("li");
      empty.className = "empty-entries";
      empty.textContent = "No notes yet. Be the first to sign it.";
      entriesList.append(empty);
      return;
    }

    entries.forEach((entry) => {
      const item = document.createElement("li");
      item.className = "guestbook-entry";

      const meta = document.createElement("div");
      meta.className = "entry-meta";
      const name = document.createElement("strong");
      name.className = "entry-name";
      name.textContent = entry.name;
      const date = document.createElement("time");
      date.className = "entry-date";
      date.dateTime = entry.createdAt;
      date.textContent = new Intl.DateTimeFormat("en-US", {
        month: "short",
        day: "numeric",
        year: "numeric",
      }).format(new Date(entry.createdAt));
      meta.append(name, date);

      const message = document.createElement("p");
      message.className = "entry-message";
      message.textContent = entry.message;

      const remove = document.createElement("button");
      remove.className = "delete-entry";
      remove.type = "button";
      remove.textContent = "delete";
      remove.addEventListener("click", () => {
        saveEntries(getStoredEntries().filter((stored) => stored.id !== entry.id));
        renderEntries();
      });

      item.append(meta, message, remove);
      entriesList.append(item);
    });
  };

  const visits = Number(localStorage.getItem(STORAGE_KEYS.visits) ?? 0) + 1;
  localStorage.setItem(STORAGE_KEYS.visits, String(visits));
  counter.value = String(visits).padStart(6, "0");

  form.addEventListener("submit", (event) => {
    event.preventDefault();

    if (Number(answer.value) !== captchaAnswer) {
      status.textContent = "That math check is not quite right. Try again.";
      refreshCaptcha();
      answer.focus();
      return;
    }

    const data = new FormData(form);
    const entry = {
      id: crypto.randomUUID(),
      name: data.get("name").trim(),
      message: data.get("message").trim(),
      createdAt: new Date().toISOString(),
    };

    saveEntries([entry, ...getStoredEntries()]);
    form.reset();
    refreshCaptcha();
    status.textContent = "Signed. Thanks for leaving a note.";
    renderEntries();
  });

  refreshCaptcha();
  renderEntries();
});
