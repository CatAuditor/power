import maplibregl from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import "./style.css";

// Category slots: fixed order (CVD-validated adjacency, see PLAN.md / dataviz notes).
// Values duplicated in style.css custom props for chips — keep in sync.
const CATEGORIES = [
  { id: "low-cf-thermal",  label: "Low-CF thermal (<10%)",  light: "#2a78d6", dark: "#3987e5" },
  { id: "mid-cf-thermal",  label: "Mid-CF thermal (10–40%)", light: "#1baf7a", dark: "#199e70" },
  { id: "high-cf-thermal", label: "High-CF thermal (>40%)",  light: "#eda100", dark: "#c98500" },
  { id: "thermal-unknown", label: "Thermal (no gen data)",   light: "#008300", dark: "#008300" },
  { id: "non-thermal",     label: "Non-thermal (context)",   light: "#898781", dark: "#898781" },
];

const PM_GROUPS = [
  { id: "cc", label: "Combined cycle", test: (p) => p.pm_codes.some((c) => ["CA", "CT", "CS", "CC"].includes(c)) },
  { id: "gt", label: "Peaker (GT/IC)", test: (p) => p.pm_codes.some((c) => ["GT", "IC"].includes(c)) },
  { id: "st", label: "Steam",          test: (p) => p.pm_codes.includes("ST") },
  { id: "other", label: "Other",       test: (p) => !p.pm_codes.some((c) => ["CA", "CT", "CS", "CC", "GT", "IC", "ST"].includes(c)) },
];

const MAP_STYLES = {
  light: "https://tiles.openfreemap.org/styles/positron",
  dark: "https://tiles.openfreemap.org/styles/dark",
};

const state = {
  plants: [],
  meta: {},
  theme: window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light",
  view: "map",
  search: "",
  cats: new Set(CATEGORIES.map((c) => c.id)),
  pms: new Set(PM_GROUPS.map((g) => g.id)),
  minMw: 0,
  coastal: false,
  selected: null,
  sortKey: "mw",
  sortDir: -1,
};

const $ = (sel) => document.querySelector(sel);
const esc = (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
const fmt = (v, d = 1) => (v == null ? "—" : Number(v).toFixed(d));
const fmtCf = (v) => (v == null ? "—" : (v * 100).toFixed(1) + "%");
const catOf = (id) => CATEGORIES.find((c) => c.id === id);

// ---------- filtering ----------

function filtered() {
  const q = state.search.trim().toLowerCase();
  return state.plants.filter((p) => {
    if (!state.cats.has(p.category)) return false;
    if (p.mw < state.minMw) return false;
    if (state.coastal && p.coast_km > 10) return false;
    if (q && !p.name.toLowerCase().includes(q) && !(p.county || "").toLowerCase().includes(q)) return false;
    if (!PM_GROUPS.some((g) => state.pms.has(g.id) && g.test(p))) return false;
    return true;
  });
}

function toGeoJSON(plants) {
  return {
    type: "FeatureCollection",
    features: plants.map((p) => ({
      type: "Feature",
      geometry: { type: "Point", coordinates: [p.lon, p.lat] },
      properties: { id: p.id, name: p.name, mw: p.mw, cf: p.cf, category: p.category },
    })),
  };
}

// ---------- map ----------

let map;
let hoverPopup;

function colorExpr() {
  const pairs = CATEGORIES.flatMap((c) => [c.id, state.theme === "dark" ? c.dark : c.light]);
  return ["match", ["get", "category"], ...pairs, "#888888"];
}

function addDataLayers() {
  if (map.getSource("plants")) return;
  map.addSource("plants", { type: "geojson", data: toGeoJSON(filtered()), promoteId: "id" });

  const ring = state.theme === "dark" ? "#0d0d0d" : "#ffffff";
  // context layer first (small gray), candidates on top
  map.addLayer({
    id: "plants-context",
    type: "circle",
    source: "plants",
    filter: ["==", ["get", "category"], "non-thermal"],
    paint: {
      "circle-radius": 3,
      "circle-color": colorExpr(),
      "circle-opacity": 0.55,
      "circle-stroke-width": 0.5,
      "circle-stroke-color": ring,
    },
  });
  map.addLayer({
    id: "plants-main",
    type: "circle",
    source: "plants",
    filter: ["!=", ["get", "category"], "non-thermal"],
    paint: {
      "circle-radius": ["min", 16, ["+", 3, ["*", 0.32, ["sqrt", ["get", "mw"]]]]],
      "circle-color": colorExpr(),
      "circle-opacity": 0.92,
      "circle-stroke-width": ["case", ["boolean", ["feature-state", "selected"], false], 3, 1.5],
      "circle-stroke-color": ["case", ["boolean", ["feature-state", "selected"], false], state.theme === "dark" ? "#ffffff" : "#0b0b0b", ring],
    },
  });

  for (const layer of ["plants-main", "plants-context"]) {
    map.on("mousemove", layer, (e) => {
      map.getCanvas().style.cursor = "pointer";
      const f = e.features[0];
      const p = f.properties;
      hoverPopup
        .setLngLat(f.geometry.coordinates)
        .setHTML(
          `<div class="tt-name">${esc(p.name)}</div>
           <div class="tt-row">${Number(p.mw).toLocaleString()} MW · CF ${p.cf != null && p.cf !== "null" ? (p.cf * 100).toFixed(1) + "%" : "—"}</div>
           <div class="tt-row tt-cat"><span class="dot" style="background:${catColor(p.category)}"></span>${esc(catOf(p.category)?.label ?? p.category)}</div>`
        )
        .addTo(map);
    });
    map.on("mouseleave", layer, () => {
      map.getCanvas().style.cursor = "";
      hoverPopup.remove();
    });
    map.on("click", layer, (e) => {
      const id = e.features[0].properties.id;
      selectPlant(state.plants.find((p) => p.id === id), { fly: false });
    });
  }
}

function catColor(id) {
  const c = catOf(id);
  return c ? (state.theme === "dark" ? c.dark : c.light) : "#888";
}

function refreshMapData() {
  const src = map?.getSource("plants");
  if (src) src.setData(toGeoJSON(filtered()));
}

function initMap() {
  map = new maplibregl.Map({
    container: "map",
    style: MAP_STYLES[state.theme],
    center: [-119.3, 36.3],
    zoom: 5.35,
    attributionControl: { compact: false },
  });
  map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
  hoverPopup = new maplibregl.Popup({ closeButton: false, closeOnClick: false, offset: 10, maxWidth: "280px" });
  map.on("load", addDataLayers);
  // re-add layers after any style swap
  map.on("styledata", () => { if (map.isStyleLoaded()) addDataLayers(); });
}

function setTheme(theme) {
  state.theme = theme;
  document.documentElement.dataset.theme = theme;
  if (map) {
    map.setStyle(MAP_STYLES[theme]);
    map.once("idle", () => { addDataLayers(); refreshMapData(); applySelection(); });
  }
  renderLegend();
  renderTable();
  if (state.selected) renderDetail(state.selected);
}

// ---------- selection / detail panel ----------

let selectedFeatureId = null;

function applySelection() {
  if (selectedFeatureId != null && map?.getSource("plants")) {
    map.setFeatureState({ source: "plants", id: selectedFeatureId }, { selected: true });
  }
}

function selectPlant(plant, { fly = true } = {}) {
  if (!plant) return;
  if (selectedFeatureId != null && map?.getSource("plants")) {
    map.setFeatureState({ source: "plants", id: selectedFeatureId }, { selected: false });
  }
  state.selected = plant;
  selectedFeatureId = plant.id;
  applySelection();
  renderDetail(plant);
  $("#detail").hidden = false;
  if (fly && state.view === "map" && map) {
    map.flyTo({ center: [plant.lon, plant.lat], zoom: Math.max(map.getZoom(), 9), duration: 800 });
  }
}

function closeDetail() {
  if (selectedFeatureId != null && map?.getSource("plants")) {
    map.setFeatureState({ source: "plants", id: selectedFeatureId }, { selected: false });
  }
  state.selected = null;
  selectedFeatureId = null;
  $("#detail").hidden = true;
}

function renderDetail(p) {
  const rows = [
    ["Capacity", `${p.mw.toLocaleString()} MW · ${p.units} unit${p.units > 1 ? "s" : ""}`],
    ["Capacity factor (2024)", fmtCf(p.cf)],
    ["Technology", p.tech.join("; ") || p.prime_movers.join("; ") || "—"],
    ["Distance to coast", `${fmt(p.coast_km)} km`],
    ["Water source", p.water ?? "—"],
    ["County", p.county ?? "—"],
    ["First unit online", p.op_year ?? "—"],
    ["Planned retirement", p.retire_year ?? "—"],
    ["CAISO LMP node(s)", p.lmp_nodes.length ? p.lmp_nodes.join(", ") : "—"],
    ["CAISO resource ID(s)", p.resource_ids.length ? `${p.resource_ids.join(", ")} (${p.res_method} match)` : "not resolved"],
    ["Bid-gap analysis", p.bid_gap ?? "pending (PLAN.md M3)"],
  ];
  $("#detail").innerHTML = `
    <button class="detail-close btn" aria-label="Close details">✕</button>
    <div class="detail-cat"><span class="dot" style="background:${catColor(p.category)}"></span>${esc(catOf(p.category)?.label ?? p.category)}</div>
    <h2>${esc(p.name)}</h2>
    <div class="detail-sub">${p.city ? esc(p.city) + ", " : ""}CA · EIA plant ${p.id}</div>
    <dl>${rows.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join("")}</dl>`;
  $(".detail-close").addEventListener("click", closeDetail);
}

// ---------- legend / filters ----------

function renderLegend() {
  $("#legend").innerHTML = CATEGORIES.map((c) => {
    const on = state.cats.has(c.id);
    return `<button class="chip ${on ? "on" : ""}" data-cat="${c.id}" aria-pressed="${on}">
      <span class="dot" style="background:${state.theme === "dark" ? c.dark : c.light}"></span>${c.label}</button>`;
  }).join("");
  document.querySelectorAll("#legend .chip").forEach((el) =>
    el.addEventListener("click", () => {
      const id = el.dataset.cat;
      state.cats.has(id) ? state.cats.delete(id) : state.cats.add(id);
      renderLegend();
      update();
    })
  );
}

function renderPmChips() {
  $("#pm-chips").innerHTML = PM_GROUPS.map((g) => {
    const on = state.pms.has(g.id);
    return `<button class="chip ${on ? "on" : ""}" data-pm="${g.id}" aria-pressed="${on}">${g.label}</button>`;
  }).join("");
  document.querySelectorAll("#pm-chips .chip").forEach((el) =>
    el.addEventListener("click", () => {
      const id = el.dataset.pm;
      state.pms.has(id) ? state.pms.delete(id) : state.pms.add(id);
      renderPmChips();
      update();
    })
  );
}

// ---------- table view ----------

function renderTable() {
  const rows = filtered().slice().sort((a, b) => {
    const va = a[state.sortKey], vb = b[state.sortKey];
    if (va == null) return 1;
    if (vb == null) return -1;
    return (va < vb ? -1 : va > vb ? 1 : 0) * state.sortDir;
  });
  $("#plant-table tbody").innerHTML = rows.map((p) => `
    <tr data-id="${p.id}">
      <td><span class="dot" style="background:${catColor(p.category)}"></span>${esc(p.name)}</td>
      <td class="num">${p.mw.toLocaleString()}</td>
      <td class="num">${fmtCf(p.cf)}</td>
      <td>${esc(catOf(p.category)?.label ?? p.category)}</td>
      <td class="num">${fmt(p.coast_km)}</td>
      <td>${esc(p.county ?? "—")}</td>
      <td>${esc(p.water ?? "—")}</td>
    </tr>`).join("");
  document.querySelectorAll("#plant-table tbody tr").forEach((tr) =>
    tr.addEventListener("click", () => {
      const p = state.plants.find((x) => x.id === Number(tr.dataset.id));
      setView("map");
      selectPlant(p);
    })
  );
}

function setView(view) {
  state.view = view;
  $("#map").style.visibility = view === "map" ? "visible" : "hidden";
  $("#table-wrap").hidden = view !== "table";
  $("#view-toggle").textContent = view === "map" ? "Table" : "Map";
  if (view === "table") renderTable();
}

// ---------- update cycle ----------

function update() {
  const list = filtered();
  const totalMw = list.reduce((s, p) => s + p.mw, 0);
  $("#countline").textContent = `${list.length} of ${state.plants.length} plants · ${Math.round(totalMw).toLocaleString()} MW shown`;
  refreshMapData();
  applySelection();
  if (state.view === "table") renderTable();
}

// ---------- boot ----------

async function boot() {
  document.documentElement.dataset.theme = state.theme;
  const res = await fetch("plants.json");
  const data = await res.json();
  state.plants = data.plants;
  state.meta = data.meta;

  renderLegend();
  renderPmChips();
  try {
    initMap();
  } catch (err) {
    console.error("map init failed (WebGL unavailable?) — table view still works", err);
    setView("table");
  }
  update();

  $("#search").addEventListener("input", (e) => { state.search = e.target.value; update(); });
  const slider = $("#mw-slider");
  slider.addEventListener("input", () => {
    // log scale: 0..3 -> 0..1000 MW
    state.minMw = slider.value === "0" ? 0 : Math.round(10 ** Number(slider.value));
    $("#mw-label").textContent = state.minMw.toLocaleString();
    update();
  });
  $("#coastal").addEventListener("change", (e) => { state.coastal = e.target.checked; update(); });
  $("#theme-toggle").addEventListener("click", () => setTheme(state.theme === "dark" ? "light" : "dark"));
  $("#view-toggle").addEventListener("click", () => setView(state.view === "map" ? "table" : "map"));
  document.querySelectorAll("#plant-table th").forEach((th) =>
    th.addEventListener("click", () => {
      const k = th.dataset.k;
      if (state.sortKey === k) state.sortDir *= -1;
      else { state.sortKey = k; state.sortDir = k === "name" || k === "county" || k === "water" || k === "category" ? 1 : -1; }
      renderTable();
    })
  );
}

boot();
