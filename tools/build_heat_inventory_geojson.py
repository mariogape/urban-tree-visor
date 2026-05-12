"""Build slim tree-inventory GeoJSON files with sampled LST values.

This mirrors the wind inventory layer used by the visor, but for the heat
topic. It loads the municipal tree inventory, samples the LST raster at each
tree point, assigns an absolute heat class, and writes EPSG:4326 GeoJSON for
MapLibre.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd


REPO_ROOT = Path(__file__).resolve().parents[1]
LST_REPO = REPO_ROOT.parent / "LST-downscaling-to-10m-GEE"
sys.path.insert(0, str(LST_REPO))

from lst_downscaling.inventory_heat import (  # noqa: E402
    HEAT_COLORS,
    assign_heat_classes,
    load_inventory,
    normalize_inventory,
    sample_raster,
)


DEFAULT_PROJECT_DIR = Path("G:/Unidades compartidas/6. Projects/Projects/26.03 INFFE")
DEFAULT_DATA_DIR = DEFAULT_PROJECT_DIR / "Datos INFFE"
DEFAULTS = {
    "aranjuez": {
        "inventory": DEFAULT_DATA_DIR / "Aranjuez arbolado/aranjuez_arbolado/aranjuez_arbolado_datos/arbolado_urbano.geojson",
        "singular": DEFAULT_DATA_DIR / "Aranjuez arbolado/aranjuez_arbolado/aranjuez_arbolado_datos/arbolado_singular.geojson",
        "lst": LST_REPO / "outputs/aranjuez/lst_mean_06-07-08_2021_2025_10m.tif",
        "output": REPO_ROOT / "data/heat_inventory_aranjuez.geojson",
    },
    "majadahonda": {
        "inventory": DEFAULT_DATA_DIR / "Majadahonda arbolado/ARBOLADO_TODO_ATRA26-30_JUNTO.shp",
        "singular": None,
        "lst": LST_REPO / "outputs/majadahonda/lst_mean_06-07-08_2021_2025_10m.tif",
        "output": REPO_ROOT / "data/heat_inventory_majadahonda.geojson",
    },
}


def _clean_value(value):
    if value is None:
        return None
    if pd.isna(value):
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    if hasattr(value, "item"):
        value = value.item()
    if isinstance(value, float) and math.isnan(value):
        return None
    return value


def _fmt_num(value, ndigits):
    value = _clean_value(value)
    if value is None:
        return None
    return round(float(value), ndigits)


def _fmt_id(value) -> str:
    value = _clean_value(value)
    if value is None:
        return ""
    try:
        number = float(value)
        if number.is_integer():
            return str(int(number))
    except (TypeError, ValueError):
        pass
    return str(value)


def build_geojson(*, municipality: str, inventory: Path, singular: Path | None, lst_tif: Path, output: Path) -> Path:
    print(f"[1/5] Leyendo inventario: {inventory}")
    raw = load_inventory(inventory, municipality=municipality, singular_path=singular)
    print(f"      Registros fuente: {len(raw):,}".replace(",", "."))

    print("[2/5] Normalizando campos")
    inv = normalize_inventory(raw, municipality=municipality)

    print(f"[3/5] Muestreando LST: {lst_tif.name}")
    sampled, coverage = sample_raster(inv, lst_tif, value_name="lst_c")
    sampled = sampled[np.isfinite(sampled["lst_c"])].copy()
    print(
        f"      LST valida: {coverage.valid:,}/{coverage.total:,} "
        f"({coverage.valid_pct:.1f}%)".replace(",", ".")
    )

    print("[4/5] Clasificando y reproyectando")
    sampled["heat_class"], _ = assign_heat_classes(sampled["lst_c"])
    sampled = sampled.to_crs("EPSG:4326")

    print(f"[5/5] Escribiendo GeoJSON: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    features = []
    for _, row in sampled.reset_index(drop=True).iterrows():
        geom = row.geometry
        if geom is None or geom.is_empty:
            continue
        heat_class = str(row["heat_class"]) if _clean_value(row["heat_class"]) is not None else None
        props = {
            "city": municipality,
            "tree_id": _fmt_id(row.get("tree_id")),
            "lst_c": _fmt_num(row["lst_c"], 1),
            "heat_class": heat_class,
            "heat_color": HEAT_COLORS.get(heat_class, "#888888"),
            "species": _clean_value(row.get("species")),
            "zone": _clean_value(row.get("zone")),
            "location": _clean_value(row.get("location")),
            "urban_type": _clean_value(row.get("urban_type")),
            "inventory_source": _clean_value(row.get("inventory_source")),
            "height_m": _fmt_num(row.get("height_m"), 1),
            "perimeter_cm": _fmt_num(row.get("perimeter_cm"), 0),
            "crown_m": _fmt_num(row.get("crown_m"), 1),
            "irrigation": _clean_value(row.get("irrigation")),
            "condition": _clean_value(row.get("condition")),
            "age_class": _clean_value(row.get("age_class")),
        }
        props = {k: v for k, v in props.items() if v not in (None, "", "nan", "<NA>")}
        features.append({
            "type": "Feature",
            "geometry": {
                "type": "Point",
                "coordinates": [round(float(geom.x), 6), round(float(geom.y), 6)],
            },
            "properties": props,
        })

    output.write_text(json.dumps({"type": "FeatureCollection", "features": features}, ensure_ascii=False), encoding="utf-8")
    print(f"      {len(features):,} features · {output.stat().st_size / 1_000_000:.1f} MB".replace(",", "."))
    return output


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--municipality", required=True, choices=sorted(DEFAULTS))
    parser.add_argument("--inventory", default=None)
    parser.add_argument("--singular", default=None)
    parser.add_argument("--lst-tif", default=None)
    parser.add_argument("--output", default=None)
    args = parser.parse_args(argv)

    defaults = DEFAULTS[args.municipality]
    inventory = Path(args.inventory) if args.inventory else defaults["inventory"]
    singular = Path(args.singular) if args.singular else defaults["singular"]
    lst_tif = Path(args.lst_tif) if args.lst_tif else defaults["lst"]
    output = Path(args.output) if args.output else defaults["output"]

    if singular is not None and not singular.exists():
        singular = None

    build_geojson(
        municipality=args.municipality.title(),
        inventory=inventory,
        singular=singular,
        lst_tif=lst_tif,
        output=output,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
