// Wind-visor / urban climate prototype
// MapLibre + PMTiles raster overlays + GeoJSON inventory points

const EXPOSURE_BANDS = [
  { id: "muybajo", label: "Muy bajo", color: "#2166ac", range: [0, 20] },
  { id: "bajo", label: "Bajo", color: "#67a9cf", range: [20, 40] },
  { id: "medio", label: "Medio", color: "#d4c95a", range: [40, 60] },
  { id: "alto", label: "Alto", color: "#ef8a62", range: [60, 80] },
  { id: "muyalto", label: "Muy alto", color: "#b2182b", range: [80, 100.001] },
];

const HEAT_BANDS = [
  { id: "muyfresco", label: "Muy fresco", color: "#234c6a", range: [-Infinity, 35] },
  { id: "fresco", label: "Fresco", color: "#5f9fb9", range: [35, 40] },
  { id: "medio", label: "Medio", color: "#c9dfb1", range: [40, 45] },
  { id: "caliente", label: "Caliente", color: "#e68b53", range: [45, 50] },
  { id: "muycaliente", label: "Muy caliente", color: "#a83a32", range: [50, Infinity] },
];

const BASEMAPS = {
  osm: {
    type: "raster",
    tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"],
    tileSize: 256,
    attribution: "© OpenStreetMap contributors",
    maxzoom: 19,
  },
  positron: {
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
  dark: {
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

const protocol = new pmtiles.Protocol();
maplibregl.addProtocol("pmtiles", protocol.tile);

const EXPOSURE_URL = "data/exposure.pmtiles";
const GEOJSON_URL = "data/inventario.geojson";
const PRIORITY_HEIGHT_CLASSES = ["Grande (9 a 15 m.)", "Ejemplar (Más de 15 m.)"];

const TOPICS = {
  heat: {
    label: "Isla de calor",
    defaultCity: "aranjuez",
    cities: {
      aranjuez: {
        label: "Aranjuez",
        layerId: "heat-aranjuez",
        sourceId: "heat-aranjuez",
        url: "data/heat_aranjuez.pmtiles",
        inventoryUrl: "data/heat_inventory_aranjuez.geojson",
      },
      majadahonda: {
        label: "Majadahonda",
        layerId: "heat-majadahonda",
        sourceId: "heat-majadahonda",
        url: "data/heat_majadahonda.pmtiles",
        inventoryUrl: "data/heat_inventory_majadahonda.geojson",
      },
    },
  },
  wind: {
    label: "Viento",
    defaultCity: "caceres",
    cities: {
      caceres: {
        label: "Cáceres",
        layerId: "exposure",
        sourceId: "exposure",
        url: EXPOSURE_URL,
      },
    },
  },
};

let inventoryGeojson = null;
let heatInventoryGeojsonByCity = {};
let map = null;
let activeTopic = null;
let activeCityByTopic = {
  heat: TOPICS.heat.defaultCity,
  wind: TOPICS.wind.defaultCity,
};

const filterState = new Set(EXPOSURE_BANDS.map((band) => band.id));
const heatFilterState = new Set(HEAT_BANDS.map((band) => band.id));

function buildStyle(basemapId) {
  const bm = BASEMAPS[basemapId] || BASEMAPS.osm;
  return {
    version: 8,
    glyphs: "https://demotiles.maplibre.org/font/{fontstack}/{range}.pbf",
    sources: {
      basemap: bm,
      exposure: {
        type: "raster",
        url: "pmtiles://" + EXPOSURE_URL,
        tileSize: 256,
      },
      "heat-aranjuez": {
        type: "raster",
        url: "pmtiles://" + TOPICS.heat.cities.aranjuez.url,
        tileSize: 256,
      },
      "heat-majadahonda": {
        type: "raster",
        url: "pmtiles://" + TOPICS.heat.cities.majadahonda.url,
        tileSize: 256,
      },
      trees: {
        type: "geojson",
        data: { type: "FeatureCollection", features: [] },
        cluster: true,
        clusterMaxZoom: 13,
        clusterRadius: 40,
      },
      "heat-trees": {
        type: "geojson",
        data: { type: "FeatureCollection", features: [] },
        cluster: true,
        clusterMaxZoom: 13,
        clusterRadius: 40,
      },
    },
    layers: [
      { id: "background", type: "background", paint: { "background-color": "#e6ece4" } },
      { id: "basemap", type: "raster", source: "basemap" },
      {
        id: "exposure",
        type: "raster",
        source: "exposure",
        layout: { visibility: "none" },
        paint: { "raster-opacity": 0.75 },
      },
      {
        id: "heat-aranjuez",
        type: "raster",
        source: "heat-aranjuez",
        layout: { visibility: "none" },
        paint: { "raster-opacity": 0.7 },
      },
      {
        id: "heat-majadahonda",
        type: "raster",
        source: "heat-majadahonda",
        layout: { visibility: "none" },
        paint: { "raster-opacity": 0.7 },
      },
      {
        id: "heat-tree-clusters",
        type: "circle",
        source: "heat-trees",
        filter: ["has", "point_count"],
        layout: { visibility: "none" },
        paint: {
          "circle-color": "#a83a32",
          "circle-radius": ["step", ["get", "point_count"], 14, 50, 18, 200, 22, 1000, 28],
          "circle-opacity": 0.86,
          "circle-stroke-width": 1.5,
          "circle-stroke-color": "#ffffff",
        },
      },
      {
        id: "heat-tree-cluster-count",
        type: "symbol",
        source: "heat-trees",
        filter: ["has", "point_count"],
        layout: {
          visibility: "none",
          "text-field": "{point_count_abbreviated}",
          "text-size": 12,
          "text-font": ["Open Sans Regular", "Arial Unicode MS Regular"],
        },
        paint: { "text-color": "#ffffff" },
      },
      {
        id: "heat-tree-points",
        type: "circle",
        source: "heat-trees",
        filter: ["!", ["has", "point_count"]],
        layout: { visibility: "none" },
        paint: {
          "circle-color": [
            "match",
            ["get", "heat_class"],
            "Muy fresco", "#234c6a",
            "Fresco", "#5f9fb9",
            "Medio", "#c9dfb1",
            "Caliente", "#e68b53",
            "Muy caliente", "#a83a32",
            "#888888",
          ],
          "circle-radius": ["interpolate", ["linear"], ["zoom"], 12, 1.8, 14, 2.8, 16, 4.4, 18, 6.8],
          "circle-stroke-color": "#ffffff",
          "circle-stroke-width": 0.7,
          "circle-opacity": 0.93,
        },
      },
      {
        id: "tree-clusters",
        type: "circle",
        source: "trees",
        filter: ["has", "point_count"],
        layout: { visibility: "none" },
        paint: {
          "circle-color": "#426331",
          "circle-radius": ["step", ["get", "point_count"], 14, 50, 18, 200, 22, 1000, 28],
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
          visibility: "none",
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
        layout: { visibility: "none" },
        paint: {
          "circle-color": [
            "match",
            ["get", "exposure_class"],
            "Muy bajo", "#2166ac",
            "Bajo", "#67a9cf",
            "Medio", "#d4c95a",
            "Alto", "#ef8a62",
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

async function init() {
  map = new maplibregl.Map({
    container: "map",
    style: buildStyle("osm"),
    center: [-4.6, 40.1],
    zoom: 6.6,
    maxZoom: 19,
    minZoom: 5,
    attributionControl: { compact: true },
  });
  map.addControl(new maplibregl.NavigationControl({ showCompass: true }), "top-right");
  map.addControl(new maplibregl.ScaleControl({ unit: "metric" }), "bottom-right");

  bindUi();

  map.on("load", async () => {
    try {
      const response = await fetch(GEOJSON_URL);
      inventoryGeojson = await response.json();
      map.getSource("trees").setData(inventoryGeojson);
      updateKpis(inventoryGeojson);
      buildExposureFilters(inventoryGeojson);
    } catch (error) {
      console.error("No se ha podido cargar el inventario:", error);
    }
    if (activeTopic) refreshTopicView();
  });

  map.on("click", "tree-points", (ev) => {
    if (!ev.features || !ev.features.length) return;
    showPopup(ev.features[0]);
  });

  map.on("click", "heat-tree-points", (ev) => {
    if (!ev.features || !ev.features.length) return;
    showHeatPopup(ev.features[0]);
  });

  map.on("click", "tree-clusters", (ev) => {
    const features = map.queryRenderedFeatures(ev.point, { layers: ["tree-clusters"] });
    if (!features.length) return;
    const clusterId = features[0].properties.cluster_id;
    map.getSource("trees").getClusterExpansionZoom(clusterId).then((z) => {
      map.easeTo({ center: features[0].geometry.coordinates, zoom: z });
    });
  });

  map.on("click", "heat-tree-clusters", (ev) => {
    const features = map.queryRenderedFeatures(ev.point, { layers: ["heat-tree-clusters"] });
    if (!features.length) return;
    const clusterId = features[0].properties.cluster_id;
    map.getSource("heat-trees").getClusterExpansionZoom(clusterId).then((z) => {
      map.easeTo({ center: features[0].geometry.coordinates, zoom: z });
    });
  });

  ["tree-points", "tree-clusters", "heat-tree-points", "heat-tree-clusters"].forEach((layer) => {
    map.on("mouseenter", layer, () => { map.getCanvas().style.cursor = "pointer"; });
    map.on("mouseleave", layer, () => { map.getCanvas().style.cursor = ""; });
  });
}

function bindUi() {
  document.querySelectorAll(".topic-card").forEach((button) => {
    button.addEventListener("click", () => activateTopic(button.dataset.topic));
  });

  document.getElementById("close-topic").addEventListener("click", clearActiveTopic);
  document.getElementById("city-select").addEventListener("change", (event) => {
    activeCityByTopic[activeTopic] = event.target.value;
    refreshTopicView();
  });

  document.getElementById("toggle-exposure").addEventListener("change", refreshTopicView);
  document.getElementById("opacity-exposure").addEventListener("input", (event) => {
    map.setPaintProperty("exposure", "raster-opacity", parseFloat(event.target.value));
  });

  document.getElementById("toggle-heat").addEventListener("change", refreshTopicView);
  document.getElementById("toggle-heat-trees").addEventListener("change", refreshTopicView);
  document.getElementById("only-hot-trees").addEventListener("change", applyHeatFilter);
  document.getElementById("opacity-heat").addEventListener("input", (event) => {
    const opacity = parseFloat(event.target.value);
    Object.values(TOPICS.heat.cities).forEach((city) => {
      map.setPaintProperty(city.layerId, "raster-opacity", opacity);
    });
  });

  document.getElementById("toggle-trees").addEventListener("change", refreshTopicView);
  document.getElementById("only-priority").addEventListener("change", applyFilter);

  document.getElementById("basemap-select").addEventListener("change", (event) => {
    const bm = BASEMAPS[event.target.value];
    if (!bm) return;
    map.removeLayer("basemap");
    map.removeSource("basemap");
    map.addSource("basemap", bm);
    map.addLayer({ id: "basemap", type: "raster", source: "basemap" }, firstOverlayLayerId());
  });

  document.getElementById("panel-toggle").addEventListener("click", () => {
    document.getElementById("panel").classList.toggle("hidden");
    setTimeout(() => map.resize(), 260);
  });

  if (window.lucide) lucide.createIcons();
  buildHeatFilters();
}

function activateTopic(topicId) {
  activeTopic = topicId;
  document.getElementById("topic-detail").classList.add("open");
  document.querySelectorAll(".topic-card").forEach((button) => {
    button.classList.toggle("active", button.dataset.topic === topicId);
  });
  refreshCitySelector();
  refreshTopicView();
}

function clearActiveTopic() {
  activeTopic = null;
  document.getElementById("topic-detail").classList.remove("open");
  document.querySelectorAll(".topic-card").forEach((button) => button.classList.remove("active"));
  hideAllTopicLayers();
  setTreeVisibility(false);
  setHeatTreeVisibility(false);
  updateStatus();
}

function refreshCitySelector() {
  if (!activeTopic) return;
  const select = document.getElementById("city-select");
  const currentTopic = TOPICS[activeTopic];
  select.innerHTML = "";
  Object.entries(currentTopic.cities).forEach(([id, city]) => {
    const option = document.createElement("option");
    option.value = id;
    option.textContent = city.label;
    select.appendChild(option);
  });
  select.value = activeCityByTopic[activeTopic] || currentTopic.defaultCity;
  document.getElementById("detail-title").textContent = currentTopic.label;
}

function refreshTopicView() {
  if (!activeTopic || !map) return;
  if (!map.getLayer("exposure")) {
    updateStatus();
    return;
  }
  const cityId = activeCityByTopic[activeTopic] || TOPICS[activeTopic].defaultCity;
  const city = TOPICS[activeTopic].cities[cityId];

  document.getElementById("heat-panel").classList.toggle("active", activeTopic === "heat");
  document.getElementById("wind-panel").classList.toggle("active", activeTopic === "wind");
  document.getElementById("heat-result-caption").textContent = city ? city.label : "Producto seleccionado";

  hideAllTopicLayers();
  setTreeVisibility(false);

  if (activeTopic === "heat") {
    const showHeat = document.getElementById("toggle-heat").checked;
    if (city) {
      map.setLayoutProperty(city.layerId, "visibility", showHeat ? "visible" : "none");
      loadHeatInventory(cityId).then(() => {
        applyHeatFilter();
        setHeatTreeVisibility(document.getElementById("toggle-heat-trees").checked);
      });
      fitPmtilesBounds(city.url);
    }
  }

  if (activeTopic === "wind") {
    const showExposure = document.getElementById("toggle-exposure").checked;
    map.setLayoutProperty("exposure", "visibility", showExposure ? "visible" : "none");
    setTreeVisibility(document.getElementById("toggle-trees").checked);
    applyFilter();
    fitPmtilesBounds(EXPOSURE_URL);
  }

  updateStatus();
}

function hideAllTopicLayers() {
  map.setLayoutProperty("exposure", "visibility", "none");
  Object.values(TOPICS.heat.cities).forEach((city) => {
    map.setLayoutProperty(city.layerId, "visibility", "none");
  });
}

function setTreeVisibility(visible) {
  const state = visible ? "visible" : "none";
  ["tree-points", "tree-clusters", "tree-cluster-count"].forEach((layer) => {
    map.setLayoutProperty(layer, "visibility", state);
  });
}

function setHeatTreeVisibility(visible) {
  const state = visible ? "visible" : "none";
  ["heat-tree-points", "heat-tree-clusters", "heat-tree-cluster-count"].forEach((layer) => {
    map.setLayoutProperty(layer, "visibility", state);
  });
}

function updateStatus() {
  const topicLabel = activeTopic ? TOPICS[activeTopic].label : "Selecciona una";
  const cityId = activeTopic ? activeCityByTopic[activeTopic] : null;
  const cityLabel = activeTopic && TOPICS[activeTopic].cities[cityId]
    ? TOPICS[activeTopic].cities[cityId].label
    : "-";
  document.getElementById("status-topic").textContent = topicLabel;
  document.getElementById("status-city").textContent = cityLabel;
}

function firstOverlayLayerId() {
  return "exposure";
}

async function fitPmtilesBounds(url) {
  try {
    const p = new pmtiles.PMTiles(url);
    const header = await p.getHeader();
    if (!header) return;
    map.fitBounds(
      [
        [header.minLon, header.minLat],
        [header.maxLon, header.maxLat],
      ],
      { padding: 48, animate: false }
    );
  } catch (error) {
    console.warn("No se ha podido leer cabecera PMTiles:", error);
  }
}

function updateKpis(geojson) {
  const features = geojson.features || [];
  document.getElementById("kpi-trees").textContent = features.length.toLocaleString("es-ES");
  const priority = features.filter((feature) => isPriority(feature.properties));
  document.getElementById("kpi-priority").textContent = priority.length.toLocaleString("es-ES");
}

function isPriority(properties) {
  return PRIORITY_HEIGHT_CLASSES.includes(properties.altura_clase) && properties.exposure >= 70;
}

function buildExposureFilters(geojson) {
  const counts = {};
  EXPOSURE_BANDS.forEach((band) => { counts[band.label] = 0; });
  geojson.features.forEach((feature) => {
    const exposureClass = feature.properties.exposure_class;
    if (counts[exposureClass] !== undefined) counts[exposureClass] += 1;
  });

  const grid = document.getElementById("filter-grid");
  grid.innerHTML = "";
  EXPOSURE_BANDS.forEach((band) => {
    const chip = document.createElement("div");
    chip.className = "filter-chip";
    chip.dataset.id = band.id;
    chip.innerHTML = `
      <span class="swatch" style="background:${band.color}"></span>
      <span class="lbl">${band.label}</span>
      <span class="count">${counts[band.label].toLocaleString("es-ES")}</span>
    `;
    chip.addEventListener("click", () => {
      if (filterState.has(band.id)) filterState.delete(band.id);
      else filterState.add(band.id);
      chip.classList.toggle("off", !filterState.has(band.id));
      applyFilter();
    });
    grid.appendChild(chip);
  });
}

function buildHeatFilters() {
  const grid = document.getElementById("heat-filter-grid");
  grid.innerHTML = "";
  HEAT_BANDS.forEach((band) => {
    const chip = document.createElement("div");
    chip.className = "filter-chip";
    chip.dataset.id = band.id;
    chip.innerHTML = `
      <span class="swatch" style="background:${band.color}"></span>
      <span class="lbl">${band.label}</span>
      <span class="count" data-heat-count="${band.label}">-</span>
    `;
    chip.addEventListener("click", () => {
      if (heatFilterState.has(band.id)) heatFilterState.delete(band.id);
      else heatFilterState.add(band.id);
      chip.classList.toggle("off", !heatFilterState.has(band.id));
      applyHeatFilter();
    });
    grid.appendChild(chip);
  });
}

async function loadHeatInventory(cityId) {
  if (heatInventoryGeojsonByCity[cityId]) return heatInventoryGeojsonByCity[cityId];
  const city = TOPICS.heat.cities[cityId];
  const response = await fetch(city.inventoryUrl);
  const geojson = await response.json();
  heatInventoryGeojsonByCity[cityId] = geojson;
  return geojson;
}

function applyHeatFilter() {
  if (!activeTopic || activeTopic !== "heat" || !map || !map.getSource("heat-trees")) return;
  const cityId = activeCityByTopic.heat;
  const geojson = heatInventoryGeojsonByCity[cityId];
  if (!geojson) return;

  const onlyHot = document.getElementById("only-hot-trees").checked;
  const allowedClasses = new Set(
    HEAT_BANDS.filter((band) => heatFilterState.has(band.id)).map((band) => band.label)
  );
  const filtered = {
    type: "FeatureCollection",
    features: geojson.features.filter((feature) => {
      const properties = feature.properties;
      if (!allowedClasses.has(properties.heat_class)) return false;
      if (onlyHot && properties.lst_c < 45) return false;
      return true;
    }),
  };
  map.getSource("heat-trees").setData(filtered);
  updateHeatKpis(filtered, geojson);
  setHeatTreeVisibility(document.getElementById("toggle-heat-trees").checked);
}

function updateHeatKpis(filtered, fullGeojson) {
  const features = filtered.features || [];
  const fullFeatures = fullGeojson.features || [];
  document.getElementById("kpi-heat-trees").textContent = features.length.toLocaleString("es-ES");
  const hot = fullFeatures.filter((feature) => Number(feature.properties.lst_c) >= 45).length;
  document.getElementById("kpi-heat-hot").textContent = hot.toLocaleString("es-ES");
  const valid = fullFeatures.map((feature) => Number(feature.properties.lst_c)).filter(Number.isFinite);
  const mean = valid.length ? valid.reduce((acc, value) => acc + value, 0) / valid.length : null;
  document.getElementById("kpi-heat-mean").textContent = mean == null ? "-" : mean.toFixed(1) + " °C";
  document.querySelectorAll("[data-heat-count]").forEach((el) => {
    const label = el.dataset.heatCount;
    const count = fullFeatures.filter((feature) => feature.properties.heat_class === label).length;
    el.textContent = count.toLocaleString("es-ES");
  });
}

function applyFilter() {
  if (!inventoryGeojson || !map || !map.getSource("trees")) return;
  const onlyPriority = document.getElementById("only-priority").checked;
  const allowedClasses = new Set(
    EXPOSURE_BANDS.filter((band) => filterState.has(band.id)).map((band) => band.label)
  );
  const filtered = {
    type: "FeatureCollection",
    features: inventoryGeojson.features.filter((feature) => {
      const properties = feature.properties;
      if (!allowedClasses.has(properties.exposure_class)) return false;
      if (onlyPriority && !isPriority(properties)) return false;
      return true;
    }),
  };
  map.getSource("trees").setData(filtered);
  document.getElementById("kpi-trees").textContent = filtered.features.length.toLocaleString("es-ES");
}

function showPopup(feature) {
  const properties = feature.properties;
  const tpl = document.getElementById("popup-template").firstElementChild.cloneNode(true);
  tpl.querySelectorAll("[data-field]").forEach((el) => {
    const key = el.dataset.field;
    let value = properties[key];
    if (value === undefined || value === null || value === "") value = "-";
    if (key === "exposure") value = Number(value).toFixed(1) + " / 100";
    if (key === "exposure_class") {
      const id = (value || "").toLowerCase().replace(/\s/g, "");
      el.classList.add(id);
    }
    el.textContent = value;
  });
  new maplibregl.Popup({ offset: 12, maxWidth: "320px" })
    .setLngLat(feature.geometry.coordinates)
    .setDOMContent(tpl)
    .addTo(map);
}

function showHeatPopup(feature) {
  const properties = feature.properties;
  const tpl = document.getElementById("heat-popup-template").firstElementChild.cloneNode(true);
  tpl.querySelectorAll("[data-field]").forEach((el) => {
    const key = el.dataset.field;
    let value = properties[key];
    if (value === undefined || value === null || value === "") value = "-";
    if (key === "lst_c") value = Number(value).toFixed(1) + " °C";
    if (key === "height_m" && value !== "-") value = Number(value).toFixed(1) + " m";
    if (key === "perimeter_cm" && value !== "-") value = Number(value).toFixed(0) + " cm";
    if (key === "heat_class") {
      const id = (value || "").toLowerCase().replace(/\s/g, "");
      el.classList.add(id);
    }
    el.textContent = value;
  });
  new maplibregl.Popup({ offset: 12, maxWidth: "330px" })
    .setLngLat(feature.geometry.coordinates)
    .setDOMContent(tpl)
    .addTo(map);
}

init();
