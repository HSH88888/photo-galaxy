"""Image helpers: on-the-fly thumbnails without any disk cache.

- Thumbnails come from the EXIF thumbnail embedded in JPEG files (~160px),
  read straight out of the APP1 segment (only the first few KB of the file).
- When no embedded thumbnail exists, the original is decoded at reduced
  scale (JPEG "draft" mode decodes at 1/2, 1/4 or 1/8 resolution) and resized.
- EXIF orientation is applied in both cases.
"""
import io
import struct

from PIL import Image, ImageOps

Image.MAX_IMAGE_PIXELS = 400_000_000

# EXIF orientation -> PIL transpose operation(s)
_ORIENT_OPS = {
    2: [Image.Transpose.FLIP_LEFT_RIGHT],
    3: [Image.Transpose.ROTATE_180],
    4: [Image.Transpose.FLIP_TOP_BOTTOM],
    5: [Image.Transpose.TRANSPOSE],
    6: [Image.Transpose.ROTATE_270],
    7: [Image.Transpose.TRANSVERSE],
    8: [Image.Transpose.ROTATE_90],
}


def _parse_tiff(t):
    """Parse a TIFF/EXIF block. Returns (thumbnail_bytes_or_None, orientation)."""
    if len(t) < 8:
        return None, 1
    if t[:2] == b"II":
        e = "<"
    elif t[:2] == b"MM":
        e = ">"
    else:
        return None, 1

    def u16(o):
        return struct.unpack(e + "H", t[o:o + 2])[0]

    def u32(o):
        return struct.unpack(e + "I", t[o:o + 4])[0]

    orient = 1
    ifd0 = u32(4)
    n0 = u16(ifd0)
    for k in range(n0):
        o = ifd0 + 2 + 12 * k
        if u16(o) == 0x0112:
            orient = u16(o + 8) or 1
    next_ptr = ifd0 + 2 + 12 * n0
    if next_ptr + 4 > len(t):
        return None, orient
    ifd1 = u32(next_ptr)
    if not ifd1 or ifd1 + 2 > len(t):
        return None, orient
    off = length = None
    for k in range(u16(ifd1)):
        o = ifd1 + 2 + 12 * k
        tag, typ = u16(o), u16(o + 2)
        val = u32(o + 8) if typ == 4 else u16(o + 8)
        if tag == 0x0201:
            off = val
        elif tag == 0x0202:
            length = val
    if off and length and off + length <= len(t):
        data = t[off:off + length]
        if data[:2] == b"\xff\xd8":
            return data, orient
    return None, orient


def read_exif_thumb(path):
    """Return (embedded_thumbnail_jpeg_bytes_or_None, orientation) for a JPEG."""
    try:
        with open(path, "rb") as f:
            if f.read(2) != b"\xff\xd8":
                return None, 1
            while True:
                hdr = f.read(4)
                if len(hdr) < 4 or hdr[0] != 0xFF:
                    return None, 1
                marker = hdr[1]
                if marker in (0xDA, 0xD9):  # start of scan / end of image
                    return None, 1
                seglen = struct.unpack(">H", hdr[2:4])[0]
                if marker == 0xE1:
                    seg = f.read(seglen - 2)
                    if seg[:6] == b"Exif\x00\x00":
                        return _parse_tiff(seg[6:])
                else:
                    f.seek(seglen - 2, 1)
    except (OSError, struct.error, IndexError):
        return None, 1


def _apply_orientation(im, orient):
    for op in _ORIENT_OPS.get(orient, []):
        im = im.transpose(op)
    return im


def _encode(im, quality=85):
    if im.mode not in ("RGB", "L"):
        im = im.convert("RGB")
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=quality)
    return buf.getvalue()


def resized(path, size, quality=85):
    """Decode `path` at reduced scale so the longest side is <= size. Returns JPEG bytes."""
    with Image.open(path) as im:
        if im.format == "JPEG":
            im.draft("RGB", (size, size))
        im = ImageOps.exif_transpose(im)
        im.thumbnail((size, size), Image.Resampling.BILINEAR)
        return _encode(im, quality)


def thumbnail(path):
    """Small thumbnail: EXIF embedded thumbnail if present, else reduced decode."""
    if path.lower().endswith((".jpg", ".jpeg")):
        data, orient = read_exif_thumb(path)
        if data:
            if orient in (0, 1):
                return data
            try:
                with Image.open(io.BytesIO(data)) as im:
                    im.load()
                    return _encode(_apply_orientation(im, orient))
            except OSError:
                pass
    return resized(path, 256)
