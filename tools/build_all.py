"""One-shot orchestrator: rebuild every artifact required by the visor
(``data/exposure.pmtiles`` + ``data/inventario.geojson``) from the source
layers stored in ``source/``.

Usage:
    python tools/build_all.py
    python tools/build_all.py --skip-pmtiles    # only refresh the GeoJSON
    python tools/build_all.py --skip-geojson    # only refresh the PMTiles
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from subprocess import run

REPO_ROOT = Path(__file__).resolve().parent.parent
SOURCE_DIR = REPO_ROOT / "source"
DATA_DIR = REPO_ROOT / "data"
TOOLS_DIR = REPO_ROOT / "tools"

DEFAULT_INVENTORY = SOURCE_DIR / "todo arbolado caceres prueba1.shp"
DEFAULT_EXPOSURE = SOURCE_DIR / "wind_exposure_1m.tif"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", default=str(DEFAULT_INVENTORY))
    parser.add_argument("--exposure", default=str(DEFAULT_EXPOSURE))
    parser.add_argument("--zoom-min", type=int, default=11)
    parser.add_argument("--zoom-max", type=int, default=17)
    parser.add_argument("--skip-pmtiles", action="store_true")
    parser.add_argument("--skip-geojson", action="store_true")
    args = parser.parse_args(argv)

    DATA_DIR.mkdir(parents=True, exist_ok=True)

    if not args.skip_geojson:
        print("\n=== build_inventory_geojson ===")
        rc = run([
            sys.executable, str(TOOLS_DIR / "build_inventory_geojson.py"),
            "--inventory", args.inventory,
            "--exposure", args.exposure,
            "--output", str(DATA_DIR / "inventario.geojson"),
        ]).returncode
        if rc != 0:
            return rc

    if not args.skip_pmtiles:
        print("\n=== build_exposure_pmtiles ===")
        rc = run([
            sys.executable, str(TOOLS_DIR / "build_exposure_pmtiles.py"),
            "--input", args.exposure,
            "--output", str(DATA_DIR / "exposure.pmtiles"),
            "--zoom-min", str(args.zoom_min),
            "--zoom-max", str(args.zoom_max),
        ]).returncode
        if rc != 0:
            return rc

    print("\nAll done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
