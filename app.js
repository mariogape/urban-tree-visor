// Wind-visor — Cáceres
// MapLibre + PMTiles raster overlay + GeoJSON inventory points

const EXPOSURE_BANDS = [
  { id: "muybajo",  label: "Muy bajo", color: "#2166ac", range: [0, 20] },
  { id: "bajo",     label: "Bajo",     color: "#67a9cf", range: [20, 40] },
  { id: "medio",    label: "Medio",    color: "#d4c95a", range: [40, 60] },
  { id: "alto",     label: "Alto",     color: "#ef8a62", range: [60, 80] },
  { id: "muyalto",  label: "Muy alto", color: "#b2182b", range: [80, 100.001] },
];

const BASEMAPS = {
  "osm": {
    type: "raster",
    tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"],
    tileSize: 256,
    attribution: "© OpenStreetMap contributors",
    maxzoom: 19,
  },
  "positron": {
    type: "raster",
    tiles: [
      "https://a.basemaps.cartocdn.com/light_all/{z}/{x}/{y}.png",
      "https://b.basemaps.cartocdn.com/light_all/{z}/{x}/{y}.png",
      "https://c.basemaps.cartocdn.com/light_all/{z}/{x}/{y}.png",
    ],
    tileSize: 256,
    attribution: "© OpenStreetMap contributors © Carto",
    maxzoom: 19,
  },
  "dark": {
    type: "raster",
    tiles: [
      "https://a.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}.png",
      "https://b.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}.png",
      "https://c.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}.png",
    ],
    tileSize: 256,
    attribution: "© OpenStreetMap contributors © Carto",
    maxzoom: 19,
  },
  "esri-imagery": {
    type: "raster",
    tiles: ["https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"],
    tileSize: 256,
    attribution: "© Esri, Maxar, Earthstar Geographics",
    maxzoom: 19,
  },
};

// Register the PMTiles protocol with MapLibre
const protocol = new pmtiles.Protocol();
maplibregl.addProtocol("pmtiles", protocol.tile);

const PMTILES_URL = "data/exposure.pmtiles";
const GEOJSON_URL = "data/inventario.geojson";
const PRIORITY_HEIGHT_CLASSES = ["Grande (9 a 15 m.)", "Ejemplar (Más de 15 m.)"];

let inventoryGeojson = null;
let map = null;

// ---- Build base style ----
function buildStyle(basemapId) {
  const bm = BASEMAPS[basemapId] || BASEMAPS["osm"];
  return {
    version: 8,
    glyphs: "https://demotiles.maplibre.org/font/{fontstack}/{range}.pbf",
    sources: {
      basemap: bm,
      exposure: {
        type: "raster",
        url: "pmtiles://" + PMTILES_URL,
        tileSize: 256,
      },
      trees: {
        type: "geojson",
        data: { type: "FeatureCollection", features: [] },
        cluster: true,
        clusterMaxZoom: 13,
        clusterRadius: 40,
      },
    },
    layers: [
      { id: "basemap", type: "raster", source: "basemap" },
      {
        id: "exposure",
        type: "raster",
        source: "exposure",
        paint: { "raster-opacity": 0.75 },
      },
      {
        id: "tree-clusters",
        type: "circle",
        source: "trees",
        filter: ["has", "point_count"],
        paint: {
          "circle-color": "#426331",
          "circle-radius": [
            "step", ["get", "point_count"],
            14, 50, 18, 200, 22, 1000, 28,
          ],
          "circle-opacity": 0.85,
          "circle-stroke-width": 1.5,
          "circle-stroke-color": "#ffffff",
        },
      },
      {
        id: "tree-cluster-count",
        type: "symbol",
        source: "trees",
        filter: ["has", "point_count"],
        layout: {
          "text-field": "{point_count_abbreviated}",
          "text-size": 12,
          "text-font": ["Open Sans Regular", "Arial Unicode MS Regular"],
        },
        paint: { "text-color": "#ffffff" },
      },
      {
        id: "tree-points",
        type: "circle",
        source: "trees",
        filter: ["!", ["has", "point_count"]],
        paint: {
          "circle-color": [
            "match",
            ["get", "exposure_class"],
            "Muy bajo", "#2166ac",
            "Bajo",     "#67a9cf",
            "Medio",    "#d4c95a",
            "Alto",     "#ef8a62",
            "Muy alto", "#b2182b",
            "#888888",
          ],
          "circle-radius": ["interpolate", ["linear"], ["zoom"], 12, 1.8, 14, 2.6, 16, 4, 18, 6.5],
          "circle-stroke-color": "#ffffff",
          "circle-stroke-width": 0.6,
          "circle-opacity": 0.92,
        },
      },
    ],
  };
}

// ---- Map init ----
async function init() {
  const styleObj = buildStyle("osm");
  map = new maplibregl.Map({
    container: "map",
    style: styleObj,
    center: [-6.385, 39.473],
    zoom: 12,
    maxZoom: 19,
    minZoom: 9,
    attributionControl: { compact: true },
  });
  map.addControl(new maplibregl.NavigationControl({ showCompass: true }), "top-right");
  map.addControl(new maplibregl.ScaleControl({ unit: "metric" }), "bottom-right");

  map.on("load", async () => {
    // Load inventory GeoJSON
    try {
      const r = await fetch(GEOJSON_URL);
      inventoryGeojson = await r.json();
      map.getSource("trees").setData(inventoryGeojson);
      updateKpis(inventoryGeojson);
      buildExposureFilters(inventoryGeojson);
    } catch (e) {
      console.error("No se ha podido cargar el inventario:", e);
    }

    // Read PMTiles header to fit bounds
    try {
      const p = new pmtiles.PMTiles(PMTILES_URL);
      const header = await p.getHeader();
      if (header) {
        const bounds = [
          [header.minLon, header.minLat],
          [header.maxLon, header.maxLat],
        ];
        map.fitBounds(bounds, { padding: 40, animate: false });
      }
    } catch (e) {
      console.warn("No se ha podido leer cabecera PMTiles:", e);
    }
  });

  map.on("click", "tree-points", (ev) => {
    if (!ev.features || !ev.features.length) return;
    const f = ev.features[0];
    showPopup(f);
  });

  map.on("click", "tree-clusters", (ev) => {
    const features = map.queryRenderedFeatures(ev.point, { layers: ["tree-clusters"] });
    if (!features.length) return;
    const clusterId = features[0].properties.cluster_id;
    map.getSource("trees").getClusterExpansionZoom(clusterId).then((z) => {
      map.easeTo({ center: features[0].geometry.coordinates, zoom: z });
    });
  });

  map.on("mouseenter", "tree-points", () => { map.getCanvas().style.cursor = "pointer"; });
  map.on("mouseleave", "tree-points", () => { map.getCanvas().style.cursor = ""; });
  map.on("mouseenter", "tree-clusters", () => { map.getCanvas().style.cursor = "pointer"; });
  map.on("mouseleave", "tree-clusters", () => { map.getCanvas().style.cursor = ""; });

  // ---- UI hooks ----
  document.getElementById("toggle-exposure").addEventListener("change", (e) => {
    map.setLayoutProperty("exposure", "visibility", e.target.checked ? "visible" : "none");
  });
  document.getElementById("opacity-exposure").addEventListener("input", (e) => {
    map.setPaintProperty("exposure", "raster-opacity", parseFloat(e.target.value));
  });
  document.getElementById("toggle-trees").addEventListener("change", (e) => {
    const v = e.target.checked ? "visible" : "none";
    map.setLayoutProperty("tree-points", "visibility", v);
    map.setLayoutProperty("tree-clusters", "visibility", v);
    map.setLayoutProperty("tree-cluster-count", "visibility", v);
  });
  document.getElementById("only-priority").addEventListener("change", (e) => {
    applyFilter();
  });
  document.getElementById("basemap-select").addEventListener("change", (e) => {
    const bm = BASEMAPS[e.target.value];
    if (!bm) return;
    map.removeSource("basemap");
    map.addSource("basemap", bm);
    map.removeLayer("basemap");
    map.addLayer({ id: "basemap", type: "raster", source: "basemap" }, "exposure");
  });

  document.getElementById("panel-toggle").addEventListener("click", () => {
    document.getElementById("panel").classList.toggle("hidden");
    setTimeout(() => map.resize(), 250);
  });
}

// ---- KPIs ----
function updateKpis(geojson) {
  const features = geojson.features || [];
  document.getElementById("kpi-trees").textContent = features.length.toLocaleString("es-ES");
  const priority = features.filter((f) => isPriority(f.properties));
  document.getElementById("kpi-priority").textContent = priority.length.toLocaleString("es-ES");
}

function isPriority(p) {
  return PRIORITY_HEIGHT_CLASSES.includes(p.altura_clase) && p.exposure >= 70;
}

// ---- Exposure filter chips ----
const filterState = new Set(EXPOSURE_BANDS.map((b) => b.id));

function buildExposureFilters(geojson) {
  const counts = {};
  EXPOSURE_BANDS.forEach((b) => (counts[b.label] = 0));
  geojson.features.forEach((f) => {
    const c = f.properties.exposure_class;
    if (counts[c] !== undefined) counts[c] += 1;
  });
  const grid = document.getElementById("filter-grid");
  grid.innerHTML = "";
  EXPOSURE_BANDS.forEach((b) => {
    const chip = document.createElement("div");
    chip.className = "filter-chip";
    chip.dataset.id = b.id;
    chip.innerHTML = `
      <span class="swatch" style="background:${b.color}"></span>
      <span class="lbl">${b.label}</span>
      <span class="count">${counts[b.label].toLocaleString("es-ES")}</span>
    `;
    chip.addEventListener("click", () => {
      if (filterState.has(b.id)) filterState.delete(b.id);
      else filterState.add(b.id);
      chip.classList.toggle("off", !filterState.has(b.id));
      applyFilter();
    });
    grid.appendChild(chip);
  });
}

function applyFilter() {
  if (!inventoryGeojson) return;
  const onlyPriority = document.getElementById("only-priority").checked;
  const allowedClasses = new Set(
    EXPOSURE_BANDS.filter((b) => filterState.has(b.id)).map((b) => b.label)
  );
  const filtered = {
    type: "FeatureCollection",
    features: inventoryGeojson.features.filter((f) => {
      const p = f.properties;
      if (!allowedClasses.has(p.exposure_class)) return false;
      if (onlyPriority && !isPriority(p)) return false;
      return true;
    }),
  };
  map.getSource("trees").setData(filtered);
  document.getElementById("kpi-trees").textContent =
    filtered.features.length.toLocaleString("es-ES");
}

// ---- Popup ----
function showPopup(feature) {
  const p = feature.properties;
  const tpl = document.getElementById("popup-template").firstElementChild.cloneNode(true);
  tpl.querySelectorAll("[data-field]").forEach((el) => {
    const k = el.dataset.field;
    let v = p[k];
    if (v === undefined || v === null || v === "") v = "—";
    if (k === "exposure") v = Number(v).toFixed(1) + " / 100";
    if (k === "exposure_class") {
      const id = (v || "").toLowerCase().replace(/\s/g, "");
      el.classList.add(id);
    }
    el.textContent = v;
  });
  new maplibregl.Popup({ offset: 12, maxWidth: "320px" })
    .setLngLat(feature.geometry.coordinates)
    .setDOMContent(tpl)
    .addTo(map);
}

init();
