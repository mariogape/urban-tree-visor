# wind-visor

Visor web ligero para enseñar el **mapa de exposición al viento** y el **inventario arbóreo** de Cáceres
(producto 26.03-INFFE / Actividad 1, Darwin × INFFE 2026).

Stack: **MapLibre GL JS** + **PMTiles** (capa raster) + **GeoJSON** (puntos del inventario).
Sin servidor: el visor es un único `index.html` que se sirve desde cualquier static host
(GitHub Pages, Netlify, S3, etc.).

## Estructura

```
.
├── index.html                # visor
├── style.css
├── app.js
├── data/
│   ├── exposure.pmtiles      # capa de exposición (raster pyramid colorizado)
│   ├── heat_*.pmtiles        # capas LST / isla de calor (raster pyramid colorizado)
│   ├── heat_inventory_*.geojson # inventarios arbóreos con LST muestreada
│   └── inventario.geojson    # 48 782 árboles activos con valor de exposición
├── tools/
│   ├── build_exposure_pmtiles.py
│   ├── build_heat_inventory_geojson.py
│   ├── build_heat_pmtiles.py
│   ├── build_inventory_geojson.py
│   └── build_all.py
└── source/                   # gitignored — capas fuente
    ├── wind_exposure_1m.tif
    └── todo arbolado caceres prueba1.{shp,shx,dbf,prj,cpg}
```

## Cómo usarlo (consumidor)

Abre `index.html` desde cualquier servidor estático. **No vale abrirlo con `file://`** — los
PMTiles requieren `Range:` HTTP, sólo disponible vía HTTP(S).

Local rápido:

```bash
python -m http.server 8000
# → http://localhost:8000/
```

Despliegue en GitHub Pages: activar Pages desde la rama `main`, raíz `/`. La URL final es
`https://<usuario>.github.io/wind-visor/`.

## Cómo regenerar las capas (productor)

Las capas fuente (`wind_exposure_1m.tif` y el shapefile del inventario) se colocan en `source/`
(gitignored — los binarios viven en el repo `wind_calculator` o en la carpeta INFFE de Drive).
Después:

```bash
python tools/build_all.py
```

Esto regenera ambas salidas. Para refrescar solo una:

```bash
python tools/build_all.py --skip-pmtiles    # solo el GeoJSON del inventario
python tools/build_all.py --skip-geojson    # solo el PMTiles
```

### Pipeline raster → PMTiles

`tools/build_exposure_pmtiles.py`:

1. Aplica la rampa **blue → yellow → red** sobre el raster float (NoData → alpha 0).
2. Reproyecta a Web Mercator (EPSG:3857) con `WarpedVRT`.
3. Genera la pirámide XYZ para los zooms `--zoom-min..--zoom-max` (por defecto 11–18).
4. Empaqueta los tiles PNG en un único `.pmtiles` v3.

`tools/build_heat_pmtiles.py` genera las capas de **isla de calor** con el mismo formato
runtime que la exposición al viento: PMTiles v3, teselas PNG RGBA de 256 px, NoData
transparente y reproyección previa a EPSG:3857. La escala visual actual es común para todos
los ámbitos: **34-56 °C**, para que Aranjuez y Majadahonda sean comparables.

```bash
python tools/build_heat_pmtiles.py \
  --input ../LST-downscaling-to-10m-GEE/outputs/aranjuez/lst_mean_06-07-08_2021_2025_10m.tif \
  --output data/heat_aranjuez.pmtiles \
  --name "Isla de calor - Aranjuez" \
  --vmin 34 --vmax 56
```

### Pipeline shp → GeoJSON

`tools/build_inventory_geojson.py`:

1. Lee el shapefile con encoding `cp1252` (resuelve los acentos del DBF).
2. Filtra ejemplares dados de **baja**.
3. Muestrea el raster de exposición en cada punto y añade `exposure` + `exposure_class`.
4. Reproyecta a EPSG:4326 y exporta GeoJSON con un set mínimo de atributos.

`tools/build_heat_inventory_geojson.py` genera los inventarios de Aranjuez y Majadahonda
para el visor de isla de calor. Reutiliza la lógica del repo LST para normalizar campos,
muestrear `lst_c`, asignar `heat_class` y exportar un GeoJSON ligero en EPSG:4326.

## Funcionalidades del visor

- Toggle de la capa de exposición + slider de opacidad.
- Toggle de isla de calor + selector de ámbito + slider de opacidad.
- Inventario arbóreo para isla de calor, clusterizado, filtrable por clase térmica y con popup LST.
- Toggle del inventario, **clusterizado** automáticamente al zoom-out.
- Filtro por clase de exposición (chips clicables, con conteo por clase).
- Filtro «solo árboles prioritarios» (altura ≥ Grande **y** exposición ≥ 70).
- Selector de mapa base (OSM / Carto Positron / Carto Dark Matter / Esri Imagery).
- Popup por árbol con todos los atributos del inventario y el valor de exposición.

## Dependencias del build

```
rasterio>=1.5
pyogrio>=0.11
pmtiles>=2.0
pillow>=9
matplotlib>=3.6
pyproj>=3.6
```

Las del visor se sirven desde CDN (MapLibre 4.7, pmtiles 3.0).

## Atribuciones

- Capa de exposición: Darwin Geospatial · 2026.
- Inventario: INFFE Ingeniería para el Medio Ambiente, S.L.
- Mapa base por defecto: © OpenStreetMap contributors.
