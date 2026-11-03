"""Header patch methods. Each one rewrites an MP4 in place so TikTok serves it as is.

TikTok closes these tricks from time to time, so several known variants are kept
side by side and picked in the UI.
"""

import contextlib
import io
import os
import struct
import time

import tiktok_lossless_patch as patcher

# id -> (menu label, short description)
METHODS = {
    "mvhd": ("mvhd (buwryme)", "длительность в mvhd = максимум, метка ©cmt, moov в начале"),
    "mvhd_clean": ("mvhd чистый (videocodec)", "только длительность в mvhd, moov в конце, без меток"),
    "zilem": ("Zilem / CompressBase", "mvhd первым в moov, бренды isom, метки Zilem"),
    "legacy": ("Audio inflation (старый buwryme)", "клон аудиодорожки с раздутой stsz и хвост VOID"),
    "ghost": ("Ghost frames (tiktok-quality)", "×10 пустых кадров в видеодорожке, только H.264"),
}
DEFAULT = "mvhd"

# ffmpeg output for these methods should keep moov after mdat
NO_FASTSTART = {"mvhd_clean"}
# methods that only work on H.264 video
H264_ONLY = {"ghost"}

# Zilem Method udta: ©too "Zilem Method https://zilemmethod.com local:v5.0", ©cmt "Patched by Compressbase"
ZILEM_UDTA = bytes.fromhex(
    "000000b375647461000000ab6d657461000000000000002168646c7200000000000000006d646972"
    "6170706c0000000000000000000000007e696c737400000047a9746f6f0000003f64617461000000"
    "01000000005a696c656d204d6574686f642068747470733a2f2f7a696c656d6d6574686f642e636f"
    "6d206c6f63616c3a76352e300000002fa9636d74000000276461746100000001000000005061746368"
    "656420627920436f6d707265737362617365"
)
MAC_EPOCH = 2082844800  # seconds between 1904-01-01 and 1970-01-01


def apply(method: str, path: str, source: str):
    """Patch the MP4 at path in place. source is the user's original file."""
    if method == "mvhd":
        patcher.patch_mp4_mvhd(path)
    elif method == "mvhd_clean":
        _write(path, patch_mvhd_clean(_read(path)))
    elif method == "zilem":
        _write(path, patch_zilem(_read(path)))
    elif method == "legacy":
        _patch_legacy(path, source)
    elif method == "ghost":
        _patch_ghost(path)
    else:
        raise ValueError(f"неизвестный метод: {method}")


def _read(path: str) -> bytes:
    with open(path, "rb") as f:
        return f.read()


def _write(path: str, data: bytes):
    with open(path, "wb") as f:
        f.write(data)


def _top_boxes(data) -> dict:
    boxes = {}
    for box in patcher._cb_boxes(data, 0, len(data)):
        if box["type"] in boxes:
            raise RuntimeError(f"в файле несколько {box['type'].decode('latin-1')}, такое не поддерживается")
        boxes[box["type"]] = box
    if b"moov" not in boxes or b"mdat" not in boxes:
        raise RuntimeError("в файле нет moov или mdat")
    if boxes[b"moov"]["header"] != 8:
        raise RuntimeError("64-битный размер moov не поддерживается")
    return boxes


def _mvhd_v1(body: bytes, duration: bytes = b"\xff" * 8) -> bytes:
    """mvhd payload (after the 8-byte header) as version 1 with the given duration."""
    if body[0] == 1:
        return body[:24] + duration + body[32:]
    ct, mt, ts = struct.unpack(">III", body[4:16])
    return bytes([1]) + body[1:4] + struct.pack(">QQI", ct, mt, ts) + duration + body[20:]


def _shift_offsets(moov: bytes, delta: int) -> bytes:
    if not delta:
        return moov
    mutable = bytearray(moov)
    patcher._cb_shift_offsets(mutable, 8, len(mutable), delta)
    return bytes(mutable)


def patch_mvhd_clean(data: bytes) -> bytes:
    """aminoulogie/videocodec variant: only the mvhd duration changes, nothing is added."""
    boxes = _top_boxes(data)
    moov = boxes[b"moov"]
    children = []
    for child in patcher._cb_boxes(data, moov["offset"] + 8, moov["end"]):
        raw = data[child["offset"]:child["end"]]
        if child["type"] == b"mvhd":
            raw = patcher._cb_box(b"mvhd", _mvhd_v1(raw[8:]))
        children.append(raw)
    new_moov = patcher._cb_box(b"moov", b"".join(children))
    if moov["offset"] < boxes[b"mdat"]["offset"]:
        new_moov = _shift_offsets(new_moov, len(new_moov) - moov["size"])
    return data[:moov["offset"]] + new_moov + data[moov["end"]:]


def _zilem_ftyp(ftyp: bytes) -> bytes:
    """Rewrite brands in place (same length): major isom, compatible isom/mp42/iso2/avc1."""
    out = bytearray(ftyp)
    if len(out) < 16:
        return bytes(out)
    out[8:12] = b"isom"
    pos = 16
    for brand in (b"isom", b"mp42", b"iso2", b"avc1"):
        if pos + 4 > len(out):
            break
        out[pos:pos + 4] = brand
        pos += 4
    return bytes(out)


def patch_zilem(data: bytes) -> bytes:
    """Zilem Method / CompressBase: fresh mvhd v1 first in moov, isom brands, Zilem udta, faststart."""
    boxes = _top_boxes(data)
    moov = boxes[b"moov"]
    children = list(patcher._cb_boxes(data, moov["offset"] + 8, moov["end"]))
    mvhd = next((c for c in children if c["type"] == b"mvhd"), None)
    if mvhd is None:
        raise RuntimeError("в файле нет mvhd")
    body = data[mvhd["offset"] + 8:mvhd["end"]]
    if body[0] == 1:
        timescale, next_track = struct.unpack(">I", body[20:24])[0], struct.unpack(">I", body[108:112])[0]
    else:
        timescale, next_track = struct.unpack(">I", body[12:16])[0], struct.unpack(">I", body[96:100])[0]

    now = int(time.time()) + MAC_EPOCH
    new_body = bytearray(112)
    new_body[0] = 1
    struct.pack_into(">QQI", new_body, 4, now, now, timescale)
    new_body[24:32] = b"\xff" * 8
    struct.pack_into(">IH", new_body, 32, 0x00010000, 0x0100)  # rate 1.0, volume 1.0
    for off, value in ((48, 0x00010000), (64, 0x00010000), (80, 0x40000000)):  # unity matrix
        struct.pack_into(">I", new_body, off, value)
    struct.pack_into(">I", new_body, 108, next_track or 3)

    rest = b"".join(data[c["offset"]:c["end"]] for c in children if c is not mvhd)
    new_moov = patcher._cb_box(b"moov", patcher._cb_box(b"mvhd", bytes(new_body)) + rest + ZILEM_UDTA)

    ftyp = boxes.get(b"ftyp")
    head = _zilem_ftyp(data[ftyp["offset"]:ftyp["end"]]) if ftyp else b""
    # everything except ftyp and moov keeps its order, moov goes right after ftyp
    others = [b for b in patcher._cb_boxes(data, 0, len(data)) if b["type"] not in (b"ftyp", b"moov")]
    mdat_new = len(head) + len(new_moov)
    for box in others:
        if box["type"] == b"mdat":
            break
        mdat_new += box["size"]
    new_moov = _shift_offsets(new_moov, mdat_new - boxes[b"mdat"]["offset"])
    return head + new_moov + b"".join(data[b["offset"]:b["end"]] for b in others)


def _patch_legacy(path: str, source: str):
    # built-in config only: the patcher would otherwise fetch it from a gist
    cfg = {**patcher.ENCODE_DEFAULTS, **patcher._builtin_patcher_defaults(), "method": "legacy"}
    patcher.CODEC_DUMMY_SIZES = {k.encode("latin-1"): int(v) for k, v in cfg["dummy_sizes"].items()}
    log = io.StringIO()
    with contextlib.redirect_stdout(log):
        ok = patcher.patch_mp4(path, cfg, source)
    if not ok:
        raise RuntimeError("audio inflation не сработал:\n" + log.getvalue()[-400:])


def _patch_ghost(path: str):
    from tiktok_quality import transform
    tmp = path + ".ghost"
    errors = io.StringIO()
    try:
        # it reports problems via print + sys.exit
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(errors):
            transform(path, tmp, verbose=False)
        os.replace(tmp, path)
    except (SystemExit, Exception) as exc:
        detail = errors.getvalue().strip() or repr(exc)
        raise RuntimeError(f"tiktok-quality не смог обработать файл: {detail}") from None
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
