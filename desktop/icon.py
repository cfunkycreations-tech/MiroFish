"""
MiroFish icon generator

Uses no third-party library (only the Python standard library zlib) to render the same drawing commands as:
  - PNG (for the window icon / pywebview, embedded in the code as a base64 string)
  - ICO (multiple sizes, for the Windows executable)

Artwork: a deep blue background with a cyan fish (the minimal MiroFish mark).
"""

import struct
import zlib

BG = (9, 20, 38)
BG2 = (16, 42, 74)
FISH = (255, 255, 255)
FISH_DARK = (34, 211, 238)
ACCENT = (56, 189, 248)


def _lerp(a, b, t):
    return a + (b - a) * t


def _blend(dst, src, alpha):
    return tuple(int(round(_lerp(d, s, alpha))) for d, s in zip(dst, src))


def _rounded_mask(x, y, size, radius):
    """Whether the point (x,y) lies inside the rounded rectangle."""
    r = radius
    cx = min(max(x, r), size - r)
    cy = min(max(y, r), size - r)
    dx, dy = x - cx, y - cy
    return dx * dx + dy * dy <= r * r


def _fish_alpha(x, y, size):
    """Return the fish coverage at this point (0~1); called with supersampling."""
    s = size
    u = (x - s / 2) / (s / 2)   # -1 .. 1
    v = (y - s / 2) / (s / 2)

    # Body: an ellipse
    body = (u / 0.74) ** 2 + (v / 0.40) ** 2 <= 1.0

    # Tail: a triangle at the right end (the fish faces left)
    tail = False
    if 0.42 <= u <= 0.92:
        half = 0.42 * (u - 0.42) / 0.50
        tail = abs(v) <= half

    if body or tail:
        return 1.0

    # Dorsal fin: a triangle on top of the body
    if -0.16 <= u <= 0.36 and v < 0:
        # The three edges of the triangle: the base at v=-0.33, the apex at (0.02, -0.76)
        top = -0.33 - 0.43 * (1.0 - abs(u - 0.02) / 0.34)
        if v <= -0.33 and v >= top:
            return 1.0

    return 0.0


def _pixel_color(x, y, size, ss=3):
    """Take an ss x ss supersample at (x, y) and return RGBA."""
    cover = 0.0
    fish = 0.0
    total = ss * ss
    for i in range(ss):
        for j in range(ss):
            px = x + (i + 0.5) / ss
            py = y + (j + 0.5) / ss
            if not _rounded_mask(px, py, size, size * 0.22):
                continue
            cover += 1.0
            fish += _fish_alpha(px, py, size)

    if cover == 0:
        return (0, 0, 0, 0)

    a = cover / total
    # Background: a diagonal gradient
    t = (x + y) / (2.0 * size)
    bg = tuple(int(round(_lerp(c1, c2, t))) for c1, c2 in zip(BG, BG2))

    fish_ratio = fish / total
    if fish_ratio > 0:
        # The fish body in white with an eye on the left
        c = FISH
        ex = x - size * 0.30
        ey = y - size * 0.47
        if ex * ex + ey * ey <= (size * 0.055) ** 2:
            c = ACCENT
        bg = _blend(bg, c, min(1.0, fish_ratio * 1.15))

    return (bg[0], bg[1], bg[2], int(round(a * 255)))


def render_rgba(size):
    rows = []
    for y in range(size):
        row = bytearray()
        for x in range(size):
            row.extend(_pixel_color(x, y, size))
        rows.append(bytes(row))
    return rows


def _png_chunk(tag, data):
    chunk = struct.pack('>I', len(data)) + tag + data
    return chunk + struct.pack('>I', zlib.crc32(tag + data) & 0xFFFFFFFF)


def png_bytes(size):
    rows = render_rgba(size)
    raw = b''.join(b'\x00' + r for r in rows)
    ihdr = struct.pack('>IIBBBBB', size, size, 8, 6, 0, 0, 0)
    return (b'\x89PNG\r\n\x1a\n'
            + _png_chunk(b'IHDR', ihdr)
            + _png_chunk(b'IDAT', zlib.compress(raw, 9))
            + _png_chunk(b'IEND', b''))


def ico_bytes(sizes=(16, 24, 32, 48, 64, 128, 256)):
    """Generate a multi-size ICO (PNG-compressed inside; Vista+ supports it)."""
    images = [png_bytes(s) for s in sizes]
    header = struct.pack('<HHH', 0, 1, len(images))
    offset = 6 + 16 * len(images)
    entries, blobs = b'', b''
    for s, data in zip(sizes, images):
        entries += struct.pack('<BBBBHHII', s if s < 256 else 0, s if s < 256 else 0,
                               0, 0, 1, 32, len(data), offset)
        blobs += data
        offset += len(data)
    return header + entries + blobs


if __name__ == '__main__':
    import sys
    out = sys.argv[1] if len(sys.argv) > 1 else 'icon'
    with open(out + '.ico', 'wb') as f:
        f.write(ico_bytes())
    with open(out + '.png', 'wb') as f:
        f.write(png_bytes(256))
    print('wrote', out + '.ico', out + '.png')
