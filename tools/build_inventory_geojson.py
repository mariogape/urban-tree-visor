"""Convert the Cáceres tree inventory shapefile into a slim GeoJSON ready
for MapLibre, sampling the wind exposure raster at every point.

Outputs:
- data/inventario.geojson  (EPSG:4326, only active trees, slim attribute set)

Run:
    python tools/build_inventory_geojson.py \
        --inventory source/todo_arbolado_caceres.shp \
        --exposure  source/wind_exposure_1m.tif \
        --output    data/inventario.geojson
"""

from __future__ import annotations

import argparse
import json
import warnings
from pathlib import Path

import numpy as np
import rasterio
from pyogrio import read_dataframe
from pyproj import Transformer


_FIELD_MAP = {
    "id": "id",
    "Descripci": "descripcion",
    "Nombre Cie": "nombre_cientifico",
    "Tipo de Ub": "tipo_ubicacion",
    "zona": "zona",
    "Ultima_i_1": "perimetro_clase",
    "Ultima_i_6": "altura_clase",
    "Ultima_i_7": "fecha_inspeccion",
}


def _exposure_class(value: float | None) -> str | None:
    if value is None or np.isnan(value):
        return None
    if value < 20:
        return "Muy bajo"
    if value < 40:
        return "Bajo"
    if value < 60:
        return "Medio"
    if value < 80:
        return "Alto"
    return "Muy alto"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", required=True)
    parser.add_argument("--exposure", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)

    shp_path = Path(args.inventory)
    raster_path = Path(args.exposure)
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    print(f"[1/4] Leyendo inventario: {shp_path.name}")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        df = read_dataframe(str(shp_path), encoding="cp1252")
    df = df.rename(columns={k: v for k, v in _FIELD_MAP.items() if k in df.columns})
    df["baja_flag"] = df["Baja _"].astype(str).str.lower().eq("verdadero")
    df = df.loc[~df["baja_flag"]].copy()
    print(f"      Activos: {len(df):,}".replace(",", "."))

    print(f"[2/4] Muestreando exposición desde {raster_path.name}")
    coords = list(zip(df.geometry.x.to_numpy(), df.geometry.y.to_numpy()))
    with rasterio.open(raster_path) as src:
        sampled = np.fromiter(
            (val[0] for val in src.sample(coords)),
            dtype=np.float32,
            count=len(coords),
        )
        bounds = src.bounds
    inside = (
        (df.geometry.x >= bounds.left)
        & (df.geometry.x <= bounds.right)
        & (df.geometry.y >= bounds.bottom)
        & (df.geometry.y <= bounds.top)
    ).to_numpy()
    nodata = -9999.0
    sampled = np.where(inside, sampled, np.nan)
    sampled = np.where(np.isclose(sampled, nodata), np.nan, sampled).astype(np.float32)
    df["exposure"] = sampled
    df = df.dropna(subset=["exposure"]).copy()
    print(f"      Con exposición válida: {len(df):,}".replace(",", "."))

    df["exposure_class"] = df["exposure"].apply(_exposure_class)

    print("[3/4] Reproyectando a EPSG:4326")
    transformer = Transformer.from_crs(df.crs, "EPSG:4326", always_xy=True)
    lon, lat = transformer.transform(df.geometry.x.to_numpy(), df.geometry.y.to_numpy())

    print(f"[4/4] Escribiendo GeoJSON: {out_path}")
    features = []
    keep_attrs = [
        "id",
        "descripcion",
        "nombre_cientifico",
        "tipo_ubicacion",
        "zona",
        "altura_clase",
        "perimetro_clase",
    ]
    for i, row in df.reset_index(drop=True).iterrows():
        props = {
            "exposure": round(float(row["exposure"]), 1),
            "exposure_class": row["exposure_class"],
        }
        for k in keep_attrs:
            v = row.get(k)
            if v is None or (isinstance(v, float) and np.isnan(v)):
                continue
            props[k] = v if not hasattr(v, "item") else v.item()
        fecha = row.get("fecha_inspeccion")
        if fecha is not None and not (isinstance(fecha, float) and np.isnan(fecha)):
            try:
                props["fecha_inspeccion"] = fecha.strftime("%Y-%m-%d")
            except Exception:
                pass
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [round(float(lon[i]), 6), round(float(lat[i]), 6)]},
            "properties": props,
        })

    fc = {"type": "FeatureCollection", "features": features}
    out_path.write_text(json.dumps(fc, ensure_ascii=False), encoding="utf-8")
    size_mb = out_path.stat().st_size / 1_000_000
    print(f"      {len(features):,} features · {size_mb:.1f} MB".replace(",", "."))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
