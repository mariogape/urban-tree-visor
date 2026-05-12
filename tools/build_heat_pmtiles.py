"""Convert LST / urban heat rasters into colorized raster PMTiles.

The output format mirrors the wind exposure layer used by the visor:
PMTiles v3 containing 256 px RGBA PNG tiles in Web Mercator. The browser
therefore consumes the heat layer exactly like the wind layer, without
needing GeoTIFF/COG support at runtime.

Run:
    python tools/build_heat_pmtiles.py \
        --input ../LST-downscaling-to-10m-GEE/outputs/aranjuez/lst_mean_06-07-08_2021_2025_10m.tif \
        --output data/heat_aranjuez.pmtiles \
        --name "Isla de calor - Aranjuez"
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
from matplotlib.colors import LinearSegmentedColormap, Normalize
from PIL import Image
from pmtiles.tile import Compression, TileType, zxy_to_tileid
from pmtiles.writer import Writer
from rasterio.enums import Resampling
from rasterio.warp import calculate_default_transform, reproject, transform_bounds


HEAT_CMAP = LinearSegmentedColormap.from_list(
    "lst_heat",
    ["#2166ac", "#67a9cf", "#ffffbf", "#fdae61", "#d7191c", "#5e0f15"],
)

WEB_MERC_HALF = 20037508.342789244
TILE_SIZE = 256


def _flush(*args, **kwargs):
    print(*args, **kwargs, flush=True)


def _xy_for_lonlat(lon: float, lat: float, z: int) -> tuple[int, int]:
    r = 6378137.0
    n = 2**z
    x_merc = r * math.radians(lon)
    y_merc = r * math.log(math.tan(math.pi / 4 + math.radians(lat) / 2))
    fx = (x_merc + WEB_MERC_HALF) / (2 * WEB_MERC_HALF) * n
    fy = (WEB_MERC_HALF - y_merc) / (2 * WEB_MERC_HALF) * n
    return int(math.floor(fx)), int(math.floor(fy))


def _tile_bounds_3857(z: int, x: int, y: int) -> tuple[float, float, float, float]:
    n = 2**z
    tile_size = 2 * WEB_MERC_HALF / n
    xmin = -WEB_MERC_HALF + x * tile_size
    xmax = -WEB_MERC_HALF + (x + 1) * tile_size
    ymax = WEB_MERC_HALF - y * tile_size
    ymin = WEB_MERC_HALF - (y + 1) * tile_size
    return xmin, ymin, xmax, ymax


def colorize(input_path: Path, output_path: Path, *, vmin: float, vmax: float) -> None:
    _flush(f"[1/3] Coloreando {input_path.name} -> {output_path.name} ({vmin:g}..{vmax:g} C)")
    t0 = time.time()
    with rasterio.open(input_path) as src:
        arr = src.read(1)
        valid = np.isfinite(arr)
        if src.nodata is not None:
            valid &= arr != src.nodata

        norm = Normalize(vmin=vmin, vmax=vmax, clip=True)
        rgba = HEAT_CMAP(norm(np.where(valid, arr, vmin).astype(np.float32)))
        rgba_uint8 = (rgba * 255.0 + 0.5).astype(np.uint8)
        rgba_uint8[..., 3] = np.where(valid, 255, 0).astype(np.uint8)

        profile = src.profile.copy()
        profile.update(
            {
                "driver": "GTiff",
                "dtype": "uint8",
                "count": 4,
                "nodata": None,
                "tiled": True,
                "blockxsize": 512,
                "blockysize": 512,
                "compress": "lzw",
                "interleave": "pixel",
                "photometric": "rgb",
            }
        )
        with rasterio.open(output_path, "w", **profile) as dst:
            dst.write(np.transpose(rgba_uint8, (2, 0, 1)))
    _flush(f"      OK ({time.time() - t0:.1f} s) -> {output_path.stat().st_size / 1e6:.1f} MB")


def warp_to_3857(input_path: Path, output_path: Path) -> None:
    _flush(f"[2/3] Reproyectando a EPSG:3857 -> {output_path.name}")
    t0 = time.time()
    with rasterio.open(input_path) as src:
        transform, width, height = calculate_default_transform(
            src.crs, "EPSG:3857", src.width, src.height, *src.bounds
        )
        profile = src.profile.copy()
        profile.update(
            {
                "crs": "EPSG:3857",
                "transform": transform,
                "width": width,
                "height": height,
                "compress": "lzw",
            }
        )
        with rasterio.open(output_path, "w", **profile) as dst:
            for i in range(1, src.count + 1):
                reproject(
                    source=rasterio.band(src, i),
                    destination=rasterio.band(dst, i),
                    src_transform=src.transform,
                    src_crs=src.crs,
                    dst_transform=transform,
                    dst_crs="EPSG:3857",
                    resampling=Resampling.bilinear,
                )
    _flush(f"      OK ({time.time() - t0:.1f} s) -> shape {height}x{width}")


def tile_and_pack(
    input_path: Path,
    output_path: Path,
    *,
    zoom_min: int,
    zoom_max: int,
    name: str,
    attribution: str,
    vmin: float,
    vmax: float,
) -> None:
    _flush(f"[3/3] Tilando y empaquetando -> {output_path.name}")
    t0 = time.time()
    with rasterio.open(input_path) as src:
        west, south, east, north = transform_bounds(src.crs, "EPSG:4326", *src.bounds, densify_pts=21)
        _flush(f"      Bbox 4326: lon {west:.4f}..{east:.4f} lat {south:.4f}..{north:.4f}")
        _flush(f"      Niveles: {zoom_min}..{zoom_max}")
        rgba_full = np.transpose(src.read(indexes=[1, 2, 3, 4]), (1, 2, 0))
        h, w = rgba_full.shape[:2]
        src_xmin, src_ymin, src_xmax, src_ymax = src.bounds

    pixel_w = (src_xmax - src_xmin) / w
    pixel_h = (src_ymax - src_ymin) / h
    total_seen = 0
    total_kept = 0

    with output_path.open("wb") as fh:
        pmt = Writer(fh)
        for z in range(zoom_min, zoom_max + 1):
            xmin_t, ymin_t = _xy_for_lonlat(west, north, z)
            xmax_t, ymax_t = _xy_for_lonlat(east, south, z)
            n_z = (xmax_t - xmin_t + 1) * (ymax_t - ymin_t + 1)
            kept_z = 0
            _flush(f"      z={z}: {n_z} tiles")
            for tx in range(xmin_t, xmax_t + 1):
                for ty in range(ymin_t, ymax_t + 1):
                    total_seen += 1
                    xmin, ymin, xmax, ymax = _tile_bounds_3857(z, tx, ty)
                    col_lo_f = (xmin - src_xmin) / pixel_w
                    col_hi_f = (xmax - src_xmin) / pixel_w
                    row_lo_f = (src_ymax - ymax) / pixel_h
                    row_hi_f = (src_ymax - ymin) / pixel_h

                    src_col_lo = max(0, int(math.floor(col_lo_f)))
                    src_col_hi = min(w, int(math.ceil(col_hi_f)))
                    src_row_lo = max(0, int(math.floor(row_lo_f)))
                    src_row_hi = min(h, int(math.ceil(row_hi_f)))
                    if src_col_hi <= src_col_lo or src_row_hi <= src_row_lo:
                        continue

                    src_slice = rgba_full[src_row_lo:src_row_hi, src_col_lo:src_col_hi, :]
                    if src_slice.size == 0 or not src_slice[..., 3].any():
                        continue

                    span_x = col_hi_f - col_lo_f
                    span_y = row_hi_f - row_lo_f
                    inner_w = max(1, int(round(TILE_SIZE * (src_col_hi - src_col_lo) / span_x)))
                    inner_h = max(1, int(round(TILE_SIZE * (src_row_hi - src_row_lo) / span_y)))
                    inner_x = int(round(TILE_SIZE * (src_col_lo - col_lo_f) / span_x))
                    inner_y = int(round(TILE_SIZE * (src_row_lo - row_lo_f) / span_y))
                    inner_w = min(inner_w, TILE_SIZE - inner_x)
                    inner_h = min(inner_h, TILE_SIZE - inner_y)
                    if inner_w <= 0 or inner_h <= 0:
                        continue

                    src_img = Image.fromarray(src_slice).resize((inner_w, inner_h), Image.BILINEAR)
                    canvas = Image.new("RGBA", (TILE_SIZE, TILE_SIZE), (0, 0, 0, 0))
                    canvas.paste(src_img, (inner_x, inner_y))
                    buf = io.BytesIO()
                    canvas.save(buf, format="PNG", optimize=False, compress_level=4)
                    pmt.write_tile(zxy_to_tileid(z, tx, ty), buf.getvalue())
                    kept_z += 1
            total_kept += kept_z
            _flush(f"            escritos {kept_z}/{n_z}")

        center_lon = (west + east) / 2
        center_lat = (south + north) / 2
        pmt.finalize(
            {
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
            },
            {
                "name": name,
                "attribution": attribution,
                "type": "overlay",
                "format": "png",
                "value_unit": "degC",
                "vmin": str(vmin),
                "vmax": str(vmax),
            },
        )

    size_mb = output_path.stat().st_size / 1e6
    _flush(f"      OK ({time.time() - t0:.1f} s) -> {total_kept}/{total_seen} tiles, {size_mb:.1f} MB")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--zoom-min", type=int, default=11)
    parser.add_argument("--zoom-max", type=int, default=16)
    parser.add_argument("--vmin", type=float, default=34.0)
    parser.add_argument("--vmax", type=float, default=56.0)
    parser.add_argument("--name", default="Isla de calor")
    parser.add_argument("--attribution", default="Darwin Geospatial")
    parser.add_argument("--keep-temp", action="store_true")
    args = parser.parse_args(argv)

    input_path = Path(args.input).resolve()
    output_path = Path(args.output).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_dir = output_path.parent / "_tmp_heat"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    rgba_src = tmp_dir / f"{output_path.stem}_rgba_src.tif"
    rgba_3857 = tmp_dir / f"{output_path.stem}_rgba_3857.tif"

    colorize(input_path, rgba_src, vmin=args.vmin, vmax=args.vmax)
    warp_to_3857(rgba_src, rgba_3857)
    tile_and_pack(
        rgba_3857,
        output_path,
        zoom_min=args.zoom_min,
        zoom_max=args.zoom_max,
        name=args.name,
        attribution=args.attribution,
        vmin=args.vmin,
        vmax=args.vmax,
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
