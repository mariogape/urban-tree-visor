"""Convert the float exposure raster into a PMTiles archive of RGBA PNG tiles
ready to be served by MapLibre via the protomaps PMTiles plugin.

Pipeline (each step writes a temp file on disk so we can recover/observe):

  1. Open the float raster and apply the BYR colormap → RGBA uint8
     (alpha = 0 where NoData). Write `_tmp_rgba_src.tif` in the source CRS.
  2. gdalwarp the RGBA raster to EPSG:3857 with bilinear resampling.
     Write `_tmp_rgba_3857.tif`.
  3. For each zoom in `[zoom_min, zoom_max]`, walk the XYZ tile grid covering
     the warped raster bbox, read each 256×256 tile from disk, encode PNG,
     append to the PMTiles archive.

Output: ``data/exposure.pmtiles``.

Run:
    python -u tools/build_exposure_pmtiles.py \
        --input  source/wind_exposure_1m.tif \
        --output data/exposure.pmtiles \
        --zoom-min 11 --zoom-max 17
"""

from __future__ import annotations

import argparse
import io
import math
import sys
import time
from pathlib import Path

import numpy as np
import rasterio
from PIL import Image
from matplotlib.colors import LinearSegmentedColormap, Normalize
from pmtiles.tile import (
    Compression,
    TileType,
    zxy_to_tileid,
)
from pmtiles.writer import Writer
from rasterio.enums import Resampling
from rasterio.warp import calculate_default_transform, reproject, transform_bounds


EXPOSURE_CMAP = LinearSegmentedColormap.from_list(
    "expo_byr",
    ["#2166ac", "#67a9cf", "#d1e5f0", "#f7f7d4", "#fddbc7", "#ef8a62", "#b2182b"],
)

WEB_MERC_HALF = 20037508.342789244
TILE_SIZE = 256


def _flush(*args, **kwargs):
    print(*args, **kwargs, flush=True)


def _xy_for_lonlat(lon: float, lat: float, z: int) -> tuple[int, int]:
    R = 6378137.0
    n = 2 ** z
    x_merc = R * math.radians(lon)
    y_merc = R * math.log(math.tan(math.pi / 4 + math.radians(lat) / 2))
    fx = (x_merc + WEB_MERC_HALF) / (2 * WEB_MERC_HALF) * n
    fy = (WEB_MERC_HALF - y_merc) / (2 * WEB_MERC_HALF) * n
    return int(math.floor(fx)), int(math.floor(fy))


def _tile_bounds_3857(z: int, x: int, y: int) -> tuple[float, float, float, float]:
    n = 2 ** z
    tile_size = 2 * WEB_MERC_HALF / n
    xmin = -WEB_MERC_HALF + x * tile_size
    xmax = -WEB_MERC_HALF + (x + 1) * tile_size
    ymax = WEB_MERC_HALF - y * tile_size
    ymin = WEB_MERC_HALF - (y + 1) * tile_size
    return xmin, ymin, xmax, ymax


def step1_colorize(input_path: Path, rgba_src_path: Path) -> dict:
    _flush(f"[1/3] Coloreando {input_path.name} → {rgba_src_path.name}")
    t0 = time.time()
    with rasterio.open(input_path) as src:
        arr = src.read(1)
        nodata = src.nodata
        crs = src.crs
        transform = src.transform
        width = src.width
        height = src.height
        bounds = src.bounds

    valid = np.isfinite(arr)
    if nodata is not None:
        valid &= arr != nodata

    norm = Normalize(vmin=0.0, vmax=100.0, clip=True)
    rgba = EXPOSURE_CMAP(norm(np.where(valid, arr, 0.0).astype(np.float32)))
    rgba_uint8 = (rgba * 255.0 + 0.5).astype(np.uint8)
    rgba_uint8[..., 3] = np.where(valid, 255, 0).astype(np.uint8)
    bands = np.transpose(rgba_uint8, (2, 0, 1))

    profile = {
        "driver": "GTiff",
        "dtype": "uint8",
        "width": width,
        "height": height,
        "count": 4,
        "crs": crs,
        "transform": transform,
        "tiled": True,
        "blockxsize": 512,
        "blockysize": 512,
        "compress": "lzw",
        "interleave": "pixel",
        "photometric": "rgb",
    }
    with rasterio.open(rgba_src_path, "w", **profile) as dst:
        dst.write(bands)
    _flush(f"      OK ({time.time()-t0:.1f} s) → {rgba_src_path.stat().st_size/1e6:.1f} MB")
    return {"crs": crs, "bounds": bounds}


def step2_warp_to_3857(rgba_src_path: Path, rgba_3857_path: Path) -> dict:
    _flush(f"[2/3] Reproyectando a EPSG:3857 → {rgba_3857_path.name}")
    t0 = time.time()
    with rasterio.open(rgba_src_path) as src:
        dst_crs = "EPSG:3857"
        transform, width, height = calculate_default_transform(
            src.crs, dst_crs, src.width, src.height, *src.bounds
        )
        profile = src.profile.copy()
        profile.update({
            "crs": dst_crs,
            "transform": transform,
            "width": width,
            "height": height,
            "compress": "lzw",
        })
        with rasterio.open(rgba_3857_path, "w", **profile) as dst:
            for i in range(1, src.count + 1):
                reproject(
                    source=rasterio.band(src, i),
                    destination=rasterio.band(dst, i),
                    src_transform=src.transform,
                    src_crs=src.crs,
                    dst_transform=transform,
                    dst_crs=dst_crs,
                    resampling=Resampling.bilinear,
                )
    _flush(f"      OK ({time.time()-t0:.1f} s) → {rgba_3857_path.stat().st_size/1e6:.1f} MB"
           f" — shape {height}×{width}")
    return {"transform": transform, "width": width, "height": height}


def step3_tile_and_pack(
    rgba_3857_path: Path,
    output_pmtiles: Path,
    *,
    zoom_min: int,
    zoom_max: int,
    name: str,
    attribution: str,
):
    _flush(f"[3/3] Tilando y empaquetando → {output_pmtiles.name}")
    t0 = time.time()
    with rasterio.open(rgba_3857_path) as src:
        west, south, east, north = transform_bounds(
            src.crs, "EPSG:4326", *src.bounds, densify_pts=21
        )
        _flush(f"      Bbox 4326: lon {west:.4f}..{east:.4f} lat {south:.4f}..{north:.4f}")
        _flush(f"      Niveles: {zoom_min}..{zoom_max}")
        _flush(f"      Cargando raster en memoria ({src.height}×{src.width}×{src.count})...")
        t_load = time.time()
        rgba_full = src.read(indexes=[1, 2, 3, 4])  # (4, H, W) uint8
        # Convert to (H, W, 4) for PIL
        rgba_full = np.transpose(rgba_full, (1, 2, 0))
        H, W = rgba_full.shape[:2]
        src_xmin, src_ymin, src_xmax, src_ymax = src.bounds
        _flush(f"      OK ({time.time()-t_load:.1f} s) — {rgba_full.nbytes/1e6:.0f} MB en RAM")

    pixel_w = (src_xmax - src_xmin) / W
    pixel_h = (src_ymax - src_ymin) / H

    with output_pmtiles.open("wb") as fh:
        pmt = Writer(fh)

        total_seen = 0
        total_kept = 0
        for z in range(zoom_min, zoom_max + 1):
            xmin_t, ymin_t = _xy_for_lonlat(west, north, z)
            xmax_t, ymax_t = _xy_for_lonlat(east, south, z)
            n_x = xmax_t - xmin_t + 1
            n_y = ymax_t - ymin_t + 1
            n_z = n_x * n_y
            _flush(f"      z={z}: rejilla {n_x}×{n_y} = {n_z} tiles")
            t_z = time.time()
            kept_z = 0
            for tx in range(xmin_t, xmax_t + 1):
                for ty in range(ymin_t, ymax_t + 1):
                    total_seen += 1
                    xmin, ymin, xmax, ymax = _tile_bounds_3857(z, tx, ty)

                    col_lo_f = (xmin - src_xmin) / pixel_w
                    col_hi_f = (xmax - src_xmin) / pixel_w
                    row_lo_f = (src_ymax - ymax) / pixel_h
                    row_hi_f = (src_ymax - ymin) / pixel_h

                    src_col_lo = max(0, int(math.floor(col_lo_f)))
                    src_col_hi = min(W, int(math.ceil(col_hi_f)))
                    src_row_lo = max(0, int(math.floor(row_lo_f)))
                    src_row_hi = min(H, int(math.ceil(row_hi_f)))
                    if src_col_hi <= src_col_lo or src_row_hi <= src_row_lo:
                        continue

                    src_slice = rgba_full[src_row_lo:src_row_hi, src_col_lo:src_col_hi, :]
                    if src_slice.size == 0:
                        continue
                    if not src_slice[..., 3].any():
                        continue

                    pad_left = src_col_lo - col_lo_f
                    pad_top = src_row_lo - row_lo_f
                    pad_right = col_hi_f - src_col_hi
                    pad_bottom = row_hi_f - src_row_hi
                    span_x = col_hi_f - col_lo_f
                    span_y = row_hi_f - row_lo_f

                    inner_w = max(1, int(round(TILE_SIZE * (src_col_hi - src_col_lo) / span_x)))
                    inner_h = max(1, int(round(TILE_SIZE * (src_row_hi - src_row_lo) / span_y)))
                    inner_x = int(round(TILE_SIZE * pad_left / span_x))
                    inner_y = int(round(TILE_SIZE * pad_top / span_y))
                    inner_w = min(inner_w, TILE_SIZE - inner_x)
                    inner_h = min(inner_h, TILE_SIZE - inner_y)
                    if inner_w <= 0 or inner_h <= 0:
                        continue

                    src_img = Image.fromarray(src_slice)
                    src_img = src_img.resize((inner_w, inner_h), Image.BILINEAR)
                    canvas = Image.new("RGBA", (TILE_SIZE, TILE_SIZE), (0, 0, 0, 0))
                    canvas.paste(src_img, (inner_x, inner_y))

                    buf = io.BytesIO()
                    canvas.save(buf, format="PNG", optimize=False, compress_level=4)
                    pmt.write_tile(zxy_to_tileid(z, tx, ty), buf.getvalue())
                    kept_z += 1
            total_kept += kept_z
            _flush(f"            escritos {kept_z}/{n_z}  ({time.time()-t_z:.1f} s)")

        center_lon = (west + east) / 2
        center_lat = (south + north) / 2
        header = {
            "tile_type": TileType.PNG,
            "tile_compression": Compression.NONE,
            "min_zoom": zoom_min,
            "max_zoom": zoom_max,
            "min_lon_e7": int(west * 1e7),
            "min_lat_e7": int(south * 1e7),
            "max_lon_e7": int(east * 1e7),
            "max_lat_e7": int(north * 1e7),
            "center_zoom": min(zoom_max, zoom_min + 2),
            "center_lon_e7": int(center_lon * 1e7),
            "center_lat_e7": int(center_lat * 1e7),
        }
        metadata = {
            "name": name,
            "attribution": attribution,
            "type": "overlay",
            "format": "png",
        }
        pmt.finalize(header, metadata)

    size_mb = output_pmtiles.stat().st_size / 1e6
    _flush(f"      OK ({time.time()-t0:.1f} s) — {total_kept}/{total_seen} tiles · {size_mb:.1f} MB")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--zoom-min", type=int, default=11)
    parser.add_argument("--zoom-max", type=int, default=17)
    parser.add_argument("--name", default="Exposición al viento — Cáceres")
    parser.add_argument("--attribution", default="Darwin Geospatial · INFFE")
    parser.add_argument("--keep-temp", action="store_true",
                        help="Conserva los .tif intermedios (RGBA original y RGBA Web Mercator).")
    args = parser.parse_args(argv)

    input_path = Path(args.input).resolve()
    output_path = Path(args.output).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_dir = output_path.parent / "_tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)

    rgba_src = tmp_dir / "rgba_src.tif"
    rgba_3857 = tmp_dir / "rgba_3857.tif"

    if not rgba_src.exists():
        step1_colorize(input_path, rgba_src)
    else:
        _flush(f"[1/3] Reusando RGBA fuente: {rgba_src.name}")

    if not rgba_3857.exists():
        step2_warp_to_3857(rgba_src, rgba_3857)
    else:
        _flush(f"[2/3] Reusando RGBA 3857: {rgba_3857.name}")

    step3_tile_and_pack(
        rgba_3857, output_path,
        zoom_min=args.zoom_min, zoom_max=args.zoom_max,
        name=args.name, attribution=args.attribution,
    )

    if not args.keep_temp:
        for p in (rgba_src, rgba_3857):
            try:
                p.unlink()
            except OSError:
                pass
        try:
            tmp_dir.rmdir()
        except OSError:
            pass

    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass
    raise SystemExit(main())
