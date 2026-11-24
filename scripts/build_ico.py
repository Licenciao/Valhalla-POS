"""Generador determinista de g:\\IAs\\assets\\valhalla_pos.ico en Python puro.

Produce un archivo ICO (PNG-in-ICO 32-bit RGBA straight alpha) con cuatro
resoluciones (16, 32, 48, 256) usando MSAA 4x4 premultiplicado y sin chunks
temporales (tIME) para garantizar reproducibilidad bit a bit.
"""

from __future__ import annotations

import math
import os
import struct
import tempfile
import zlib
from pathlib import Path

RESOLUTIONS: tuple[int, ...] = (16, 32, 48, 256)
MSAA_SAMPLES: int = 4

# Paleta oficial Valhalla POS (RGB en [0, 1])
ROYAL_INDIGO: tuple[float, float, float] = (0x2F / 255.0, 0x3D / 255.0, 0x82 / 255.0)
DEEP_VIOLET: tuple[float, float, float] = (0x2C / 255.0, 0x28 / 255.0, 0x63 / 255.0)
SAPPHIRE_BLUE: tuple[float, float, float] = (0x3D / 255.0, 0x71 / 255.0, 0xA3 / 255.0)
WHITE: tuple[float, float, float] = (1.0, 1.0, 1.0)
EMERALD_GREEN: tuple[float, float, float] = (0x10 / 255.0, 0xB9 / 255.0, 0x81 / 255.0)

PNG_SIGNATURE: bytes = b"\x89PNG\r\n\x1a\n"
ICONDIR_SIZE: int = 6
ICONDIRENTRY_SIZE: int = 16


def _clamp(value: float, low: float, high: float) -> float:
    return low if value < low else (high if value > high else value)


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def _lerp_rgb(
    c1: tuple[float, float, float],
    c2: tuple[float, float, float],
    t: float,
) -> tuple[float, float, float]:
    t_clamped = _clamp(t, 0.0, 1.0)
    return (
        _lerp(c1[0], c2[0], t_clamped),
        _lerp(c1[1], c2[1], t_clamped),
        _lerp(c1[2], c2[2], t_clamped),
    )


def _smoothstep(edge0: float, edge1: float, x: float) -> float:
    if edge1 <= edge0:
        return 1.0 if x >= edge1 else 0.0
    t = _clamp((x - edge0) / (edge1 - edge0), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def _sdf_rounded_box(
    px: float,
    py: float,
    half_w: float,
    half_h: float,
    radius: float,
) -> float:
    qx = abs(px) - (half_w - radius)
    qy = abs(py) - (half_h - radius)
    return math.hypot(max(qx, 0.0), max(qy, 0.0)) + min(max(qx, qy), 0.0) - radius


def _dist_segment(
    px: float,
    py: float,
    ax: float,
    ay: float,
    bx: float,
    by: float,
) -> float:
    abx = bx - ax
    aby = by - ay
    denom = abx * abx + aby * aby
    if denom <= 1e-12:
        return math.hypot(px - ax, py - ay)
    t = _clamp(((px - ax) * abx + (py - ay) * aby) / denom, 0.0, 1.0)
    return math.hypot(px - (ax + t * abx), py - (ay + t * aby))


def _build_cart_segments(
    size: int,
) -> tuple[
    list[tuple[float, float, float, float, float]],
    tuple[tuple[float, float], tuple[float, float], float, float],
]:
    """Construye los segmentos y ruedas del carrito en coordenadas y-abajo [0, 1]."""
    y_handle = 0.25
    y_top = 0.325
    y_bot = 0.535 if size == 16 else 0.545
    y_chassis = 0.655

    tl = (0.31, y_top)
    tr = (0.81, y_top)
    br = (0.74, y_bot)
    bl = (0.365, y_bot)

    main_r = 0.034 if size == 16 else (0.025 if size == 32 else 0.023)

    segments: list[tuple[float, float, float, float, float]] = [
        # Manija izquierda -> borde superior izquierdo de la canastilla
        (0.185, y_handle, 0.27, y_handle, main_r),
        (0.27, y_handle, tl[0], tl[1], main_r),
        # Contorno de la canastilla (w_top > w_bot, y_top < y_bot)
        (tl[0], tl[1], tr[0], tr[1], main_r),
        (tr[0], tr[1], br[0], br[1], main_r),
        (br[0], br[1], bl[0], bl[1], main_r),
        (bl[0], bl[1], tl[0], tl[1], main_r),
        # Chasis inferior hacia las ruedas
        (bl[0], bl[1], 0.345, y_chassis, main_r),
        (0.345, y_chassis, 0.745, y_chassis, main_r),
    ]

    if size == 32:
        grid_r = 0.014
        mid_l_x = _lerp(tl[0], bl[0], 0.5)
        mid_r_x = _lerp(tr[0], br[0], 0.5)
        mid_y = _lerp(y_top, y_bot, 0.5)
        segments.append((mid_l_x, mid_y, mid_r_x, mid_y, grid_r))
        for frac in (1.0 / 3.0, 2.0 / 3.0):
            tx = _lerp(tl[0], tr[0], frac)
            bx = _lerp(bl[0], br[0], frac)
            segments.append((tx, y_top, bx, y_bot, grid_r))
    elif size >= 48:
        grid_r = 0.013
        for frac in (1.0 / 3.0, 2.0 / 3.0):
            lx = _lerp(tl[0], bl[0], frac)
            rx = _lerp(tr[0], br[0], frac)
            hy = _lerp(y_top, y_bot, frac)
            segments.append((lx, hy, rx, hy, grid_r))
        for frac in (0.25, 0.50, 0.75):
            tx = _lerp(tl[0], tr[0], frac)
            bx = _lerp(bl[0], br[0], frac)
            segments.append((tx, y_top, bx, y_bot, grid_r))

    if size == 16:
        # Centros de rueda alineados exactamente a centro de píxel (x+0.5, y+0.5)
        wl = (6.5 / 16.0, 12.5 / 16.0)
        wr = (11.5 / 16.0, 12.5 / 16.0)
        wheel_outer_r = 1.45 / 16.0
        wheel_inner_r = 0.74 / 16.0
    elif size == 32:
        wl = (13.5 / 32.0, 24.5 / 32.0)
        wr = (22.5 / 32.0, 24.5 / 32.0)
        wheel_outer_r = 0.068
        wheel_inner_r = 0.034
    else:
        wl = (0.42, 0.76)
        wr = (0.705, 0.76)
        wheel_outer_r = 0.068
        wheel_inner_r = 0.033

    return segments, (wl, wr, wheel_outer_r, wheel_inner_r)


def _sample_icon(
    u: float,
    v: float,
    size: int,
    segments: list[tuple[float, float, float, float, float]],
    wheels: tuple[tuple[float, float], tuple[float, float], float, float],
) -> tuple[float, float, float, float]:
    """Evalúa color RGBA straight alpha en el punto normalizado (u, v)."""
    margin = 0.06
    half_extent = 0.5 - margin
    corner_radius = 0.18
    aa = 0.5 / size

    sd_box = _sdf_rounded_box(u - 0.5, v - 0.5, half_extent, half_extent, corner_radius)
    bg_alpha = 1.0 - _smoothstep(-aa, aa, sd_box)
    if bg_alpha <= 0.0:
        return (0.0, 0.0, 0.0, 0.0)

    grad_t = _clamp(0.45 * u + 0.55 * v, 0.0, 1.0)
    bg_rgb = _lerp_rgb(ROYAL_INDIGO, DEEP_VIOLET, grad_t)

    border_width = max(1.0 / size, 0.028)
    border_mix = _smoothstep(-border_width - aa, -border_width + aa, sd_box)
    bg_rgb = _lerp_rgb(bg_rgb, SAPPHIRE_BLUE, border_mix)

    # Distancia mínima al armazón del carrito y a los aros de las ruedas
    sd_white = 1e9
    for ax, ay, bx, by, radius in segments:
        d = _dist_segment(u, v, ax, ay, bx, by) - radius
        if d < sd_white:
            sd_white = d

    wl, wr, wheel_outer_r, wheel_inner_r = wheels
    d_wl = math.hypot(u - wl[0], v - wl[1])
    d_wr = math.hypot(u - wr[0], v - wr[1])
    d_wheel = min(d_wl, d_wr)

    sd_white = min(sd_white, d_wheel - wheel_outer_r)
    white_cov = 1.0 - _smoothstep(-aa, aa, sd_white)
    emerald_cov = 1.0 - _smoothstep(-aa, aa, d_wheel - wheel_inner_r)

    fg_rgb = bg_rgb
    if white_cov > 0.0:
        fg_rgb = _lerp_rgb(fg_rgb, WHITE, white_cov)
    if emerald_cov > 0.0:
        fg_rgb = _lerp_rgb(fg_rgb, EMERALD_GREEN, emerald_cov)

    return (fg_rgb[0], fg_rgb[1], fg_rgb[2], bg_alpha)


def render_rgba_scanlines(size: int) -> bytes:
    """Renderiza una imagen cuadrada RGBA con MSAA 4x4 premultiplicado."""
    segments, wheels = _build_cart_segments(size)
    inv_samples = 1.0 / (MSAA_SAMPLES * MSAA_SAMPLES)
    offsets = tuple((i + 0.5) / MSAA_SAMPLES for i in range(MSAA_SAMPLES))

    raw = bytearray(size * (size * 4 + 1))
    stride = size * 4 + 1

    for iy in range(size):
        row_offset = iy * stride
        raw[row_offset] = 0  # PNG filter type 0 (None)
        for ix in range(size):
            acc_r = 0.0
            acc_g = 0.0
            acc_b = 0.0
            acc_a = 0.0

            for oy in offsets:
                v = (iy + oy) / size
                for ox in offsets:
                    u = (ix + ox) / size
                    r, g, b, a = _sample_icon(u, v, size, segments, wheels)
                    if a > 0.0:
                        acc_r += r * a
                        acc_g += g * a
                        acc_b += b * a
                        acc_a += a

            a_avg = acc_a * inv_samples
            px_offset = row_offset + 1 + ix * 4
            if a_avg > 1e-9:
                r_avg = _clamp((acc_r * inv_samples) / a_avg, 0.0, 1.0)
                g_avg = _clamp((acc_g * inv_samples) / a_avg, 0.0, 1.0)
                b_avg = _clamp((acc_b * inv_samples) / a_avg, 0.0, 1.0)
                raw[px_offset] = round(r_avg * 255.0)
                raw[px_offset + 1] = round(g_avg * 255.0)
                raw[px_offset + 2] = round(b_avg * 255.0)
                raw[px_offset + 3] = round(_clamp(a_avg, 0.0, 1.0) * 255.0)
            else:
                raw[px_offset] = 0
                raw[px_offset + 1] = 0
                raw[px_offset + 2] = 0
                raw[px_offset + 3] = 0

    return bytes(raw)


def _png_chunk(chunk_type: bytes, data: bytes) -> bytes:
    crc = zlib.crc32(chunk_type + data) & 0xFFFFFFFF
    return struct.pack(">I", len(data)) + chunk_type + data + struct.pack(">I", crc)


def encode_png_rgba(size: int, scanlines: bytes) -> bytes:
    """Empaqueta scanlines RGBA en PNG determinista sin chunks temporales (tIME)."""
    ihdr = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    compressed = zlib.compress(scanlines, level=9)
    return (
        PNG_SIGNATURE
        + _png_chunk(b"IHDR", ihdr)
        + _png_chunk(b"IDAT", compressed)
        + _png_chunk(b"IEND", b"")
    )


def build_ico_bytes(resolutions: tuple[int, ...] = RESOLUTIONS) -> bytes:
    """Construye el binario ICO completo con payload PNG-in-ICO RGBA."""
    png_payloads = [encode_png_rgba(size, render_rgba_scanlines(size)) for size in resolutions]

    icondir = struct.pack("<HHH", 0, 1, len(resolutions))
    entries = bytearray()
    offset = ICONDIR_SIZE + len(resolutions) * ICONDIRENTRY_SIZE

    for size, payload in zip(resolutions, png_payloads):
        dim_byte = size if size < 256 else 0
        entries.extend(
            struct.pack(
                "<BBBBHHII",
                dim_byte,
                dim_byte,
                0,
                0,
                1,
                32,
                len(payload),
                offset,
            )
        )
        offset += len(payload)

    return icondir + bytes(entries) + b"".join(png_payloads)


def write_atomic(target_path: Path, data: bytes) -> None:
    """Escribe datos de forma atómica en el mismo directorio destino."""
    target_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=target_path.parent,
            prefix=f".{target_path.stem}_",
            suffix=".tmp",
            delete=False,
        ) as tmp_file:
            tmp_path = Path(tmp_file.name)
            tmp_file.write(data)
            tmp_file.flush()
            os.fsync(tmp_file.fileno())
        os.replace(tmp_path, target_path)
        tmp_path = None
    finally:
        if tmp_path is not None and tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass


def verify_ico_file(ico_path: Path, expected_sizes: tuple[int, ...] = RESOLUTIONS) -> None:
    """Relee el archivo .ico desde disco y valida estructura ICO, PNG e IDAT."""
    raw = ico_path.read_bytes()
    if len(raw) < ICONDIR_SIZE:
        raise ValueError(f"Archivo ICO truncado: {len(raw)} bytes")

    reserved, img_type, count = struct.unpack_from("<HHH", raw, 0)
    if (reserved, img_type, count) != (0, 1, len(expected_sizes)):
        raise ValueError(
            f"ICONDIR inválido: reserved={reserved}, type={img_type}, count={count}"
        )

    expected_offset = ICONDIR_SIZE + len(expected_sizes) * ICONDIRENTRY_SIZE
    if expected_offset != 70:
        raise ValueError(f"Offset inicial inesperado: {expected_offset}")

    for idx, expected_size in enumerate(expected_sizes):
        entry_off = ICONDIR_SIZE + idx * ICONDIRENTRY_SIZE
        bw, bh, cc, res, planes, bpp, size_bytes, img_off = struct.unpack_from(
            "<BBBBHHII",
            raw,
            entry_off,
        )
        expected_dim = expected_size if expected_size < 256 else 0
        if bw != expected_dim or bh != expected_dim:
            raise ValueError(
                f"Entrada {idx}: dimensiones ({bw}, {bh}) != ({expected_dim}, {expected_dim})"
            )
        if cc != 0 or res != 0 or planes != 1 or bpp != 32:
            raise ValueError(
                f"Entrada {idx}: metadatos inválidos cc={cc}, res={res}, planes={planes}, bpp={bpp}"
            )
        if img_off != expected_offset:
            raise ValueError(
                f"Entrada {idx}: offset {img_off} != esperado {expected_offset}"
            )
        if img_off + size_bytes > len(raw):
            raise ValueError(f"Entrada {idx}: payload excede EOF")

        png_data = raw[img_off : img_off + size_bytes]
        expected_offset += size_bytes

        if not png_data.startswith(PNG_SIGNATURE):
            raise ValueError(f"Entrada {idx}: firma PNG inválida")

        pos = len(PNG_SIGNATURE)
        ihdr_seen = False
        iend_seen = False
        idat_parts: list[bytes] = []

        while pos < len(png_data):
            if pos + 8 > len(png_data):
                raise ValueError(f"Entrada {idx}: chunk PNG truncado")
            chunk_len = struct.unpack_from(">I", png_data, pos)[0]
            chunk_type = png_data[pos + 4 : pos + 8]
            data_start = pos + 8
            data_end = data_start + chunk_len
            crc_end = data_end + 4
            if crc_end > len(png_data):
                raise ValueError(f"Entrada {idx}: cuerpo de chunk {chunk_type!r} truncado")

            chunk_data = png_data[data_start:data_end]
            stored_crc = struct.unpack_from(">I", png_data, data_end)[0]
            calc_crc = zlib.crc32(chunk_type + chunk_data) & 0xFFFFFFFF
            if stored_crc != calc_crc:
                raise ValueError(f"Entrada {idx}: CRC inválido en chunk {chunk_type!r}")

            if chunk_type == b"tIME":
                raise ValueError(f"Entrada {idx}: contiene chunk no determinista tIME")
            if chunk_type == b"IHDR":
                w, h, bit_depth, color_type, comp, flt, interlace = struct.unpack(
                    ">IIBBBBB", chunk_data
                )
                if (w, h, bit_depth, color_type, comp, flt, interlace) != (
                    expected_size,
                    expected_size,
                    8,
                    6,
                    0,
                    0,
                    0,
                ):
                    raise ValueError(
                        f"Entrada {idx}: IHDR inválido {(w, h, bit_depth, color_type, comp, flt, interlace)}"
                    )
                ihdr_seen = True
            elif chunk_type == b"IDAT":
                idat_parts.append(chunk_data)
            elif chunk_type == b"IEND":
                iend_seen = True
                pos = crc_end
                break

            pos = crc_end

        if not ihdr_seen or not iend_seen or pos != len(png_data):
            raise ValueError(
                f"Entrada {idx}: estructura de chunks PNG incompleta (ihdr={ihdr_seen}, iend={iend_seen})"
            )

        decompressed = zlib.decompress(b"".join(idat_parts))
        expected_raw_len = expected_size * (expected_size * 4 + 1)
        if len(decompressed) != expected_raw_len:
            raise ValueError(
                f"Entrada {idx}: longitud IDAT {len(decompressed)} != {expected_raw_len}"
            )

    if expected_offset != len(raw):
        raise ValueError(f"Bytes sobrantes al final del ICO: {expected_offset} != {len(raw)}")


def main() -> int:
    repo_root = Path(__file__).resolve().parent.parent
    ico_path = repo_root / "assets" / "valhalla_pos.ico"
    ico_bytes = build_ico_bytes()
    write_atomic(ico_path, ico_bytes)
    verify_ico_file(ico_path)
    print(f"OK: {ico_path} generado y verificado ({len(ico_bytes)} bytes).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
