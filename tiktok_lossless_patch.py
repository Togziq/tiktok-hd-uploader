#!/usr/bin/env python3
"""
tiktok_patch.py
does stsz inflation trick dynamically.
all metadata fields are fully customizable.
"""

import json
import os
import shutil
import struct
import subprocess
import sys
import urllib.request
from pathlib import Path

# codec-aware dummy sample sizes from NoBlur
CODEC_DUMMY_SIZES = {
    b'avc1': 8,
    b'avc3': 8,
    b'hvc1': 16,
    b'hev1': 16,
    b'vp09': 4,
    b'av01': 4,
    b'mp4v': 8,
}


GIST_API_URL = (
    "https://api.github.com/gists/"
    "e7dd8bca2094995943a855f0af985960"
)

BUILTIN_TOML = r'''# Reverse engineered from Editing News' patcher and made open source.
[patcher]
title = ""
artist = ""
composer = ""
album = ""
date = ""
encoder = "Lavf59.27.100"
comment = "Patched by Buwryy"
comment_short = "Patched by Buwryy"
genre = ""
copyright = ""
grouping = ""
name_box_payload = "buwryy<3"
inflation_rate = 10
dummy_sample_size = 8
trailing_bytes = 184100
method = "mvhd"

[patcher.dummy_sizes]
avc1 = 8
avc2 = 8
avc3 = 8
avc4 = 8
hvc1 = 16
hev1 = 16
dvh1 = 16
dvhe = 16
dvav = 8
dva1 = 8
vp08 = 4
vp09 = 4
av01 = 4
mp4v = 8
s263 = 8
h263 = 8
jpeg = 8
mjpa = 8
mjpb = 8
ap4h = 8
ap4x = 8
'''

ENCODE_DEFAULTS = {
    "re_encode": True,
    "crf": 18,
    "codec": "h264",
}

_PATCHER_CONFIG = None


def _parse_toml(text):
    try:
        import tomllib
        return tomllib.loads(text)
    except Exception:
        pass
    data = {}
    section = data
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            cur = data
            for part in line[1:-1].strip().split("."):
                part = part.strip()
                if part:
                    cur = cur.setdefault(part, {})
            section = cur
            continue
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if value.startswith('"'):
            try:
                value = json.loads(value)
            except Exception:
                value = value.strip('"')
        elif value.lower() == "true":
            value = True
        elif value.lower() == "false":
            value = False
        else:
            try:
                value = int(value)
            except ValueError:
                try:
                    value = float(value)
                except ValueError:
                    pass
        section[key] = value
    return data


def _builtin_patcher_defaults():
    return _parse_toml(BUILTIN_TOML)["patcher"]


def load_patcher_config(reload=False):
    global _PATCHER_CONFIG
    if _PATCHER_CONFIG is not None and not reload:
        return _PATCHER_CONFIG
    cfg = _builtin_patcher_defaults()
    try:
        request = urllib.request.Request(
            GIST_API_URL,
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": "tiktok-lossless-upload",
            },
        )
        with urllib.request.urlopen(request, timeout=4) as response:
            payload = json.loads(response.read().decode("utf-8"))
            text = payload["files"]["tiktok_patcher.toml"]["content"]
            remote = _parse_toml(text)["patcher"]
        for key, value in remote.items():
            if isinstance(value, dict) and isinstance(cfg.get(key), dict):
                cfg[key].update(value)
            else:
                cfg[key] = value
    except Exception:
        pass
    _PATCHER_CONFIG = cfg
    return cfg


def get_patcher_config():
    return load_patcher_config()


def get_default_config():
    return dict(ENCODE_DEFAULTS)


DEFAULTS = {**ENCODE_DEFAULTS, **_builtin_patcher_defaults()}


def get_config(overrides: dict = None) -> dict:
    global CODEC_DUMMY_SIZES
    cfg = dict(ENCODE_DEFAULTS)
    cfg.update(load_patcher_config())
    cfg.update(ENCODE_DEFAULTS)
    dummy_sizes = cfg.get("dummy_sizes")
    if isinstance(dummy_sizes, dict) and dummy_sizes:
        CODEC_DUMMY_SIZES = {
            key.encode("latin-1"): int(value)
            for key, value in dummy_sizes.items()
        }
    if overrides:
        for key in ENCODE_DEFAULTS:
            if key in overrides:
                cfg[key] = overrides[key]
    return cfg



def read_u32be(data: bytes | bytearray, offset: int) -> int:
    return struct.unpack('>I', data[offset:offset+4])[0]


def write_u32be(data: bytearray, offset: int, value: int):
    struct.pack_into('>I', data, offset, value & 0xFFFFFFFF)


def read_u64be(data: bytes | bytearray, offset: int) -> int:
    return struct.unpack('>Q', data[offset:offset+8])[0]


def write_u64be(data: bytearray, offset: int, value: int):
    struct.pack_into('>Q', data, offset, value & 0xFFFFFFFFFFFFFFFF)


def parse_boxes(data: bytearray, start: int, end: int) -> list[dict]:
    boxes = []
    pos = start
    while pos + 8 <= end:
        raw_size = read_u32be(data, pos)
        size = raw_size
        if raw_size == 1:
            if pos + 16 > end:
                break
            hi = read_u32be(data, pos + 8)
            lo = read_u32be(data, pos + 12)
            size = (hi << 32) + lo
        elif raw_size == 0:
            size = end - pos
        if size < 8 or pos + size > end:
            break
        atype = data[pos+4:pos+8]
        boxes.append({"offset": pos, "size": size, "type": bytes(atype), "end": pos + size})
        pos += size
    return boxes


def find_box(data: bytearray, fourcc: bytes, start: int = 0, end: int = None) -> dict | None:
    if end is None:
        end = len(data)
    for box in parse_boxes(data, start, end):
        if box["type"] == fourcc:
            return box
    return None


def build_box(box_type: bytes, payload: bytes) -> bytes:
    size = 8 + len(payload)
    return struct.pack('>I', size) + box_type + payload


def build_fullbox(box_type: bytes, version: int, flags: int, payload: bytes) -> bytes:
    ver_flags = struct.pack('>I', (version << 24) | (flags & 0x00FFFFFF))
    return build_box(box_type, ver_flags + payload)


def build_hdlr(handler_type: bytes, name: str) -> bytes:
    payload = b'\x00' * 4 + handler_type + b'\x00' * 12
    payload += name.encode('utf-8') + b'\x00'
    return build_fullbox(b'hdlr', 0, 0, payload)


def build_data_atom(value: str) -> bytes:
    payload = struct.pack('>II', 1, 0) + value.encode('utf-8')
    return build_box(b'data', payload)


def build_ilst_entry(tag: bytes, value: str) -> bytes:
    return build_box(tag, build_data_atom(value))


def build_combined_udta(cfg: dict) -> bytes:
    ilst1 = b''
    tag_map = [
        ('\xa9nam', cfg.get("title")),
        ('\xa9ART', cfg.get("artist")),
        ('\xa9wrt', cfg.get("composer")),
        ('\xa9alb', cfg.get("album")),
        ('\xa9day', cfg.get("date")),
        ('\xa9too', cfg.get("encoder")),
        ('\xa9cmt', cfg.get("comment")),
        ('\xa9gen', cfg.get("genre")),
        ('cprt',    cfg.get("copyright")),
        ('\xa9grp', cfg.get("grouping")),
    ]
    for tag, val in tag_map:
        if val:
            ilst1 += build_ilst_entry(tag.encode('latin-1'), val)

    hdlr1_payload = b'\x00' * 4 + b'mdir' + b'\x00' * 12
    hdlr1_payload += b'appl\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00'
    hdlr1_box = build_fullbox(b'hdlr', 0, 0, hdlr1_payload)

    meta1_inner = hdlr1_box + build_box(b'ilst', ilst1)
    meta1_payload = b'\x00\x00\x00\x00' + meta1_inner
    meta1_box = build_fullbox(b'meta', 0, 375, meta1_payload)

    hdlr2_payload = b'\x00' * 4 + b'mdir' + b'\x00' * 12
    hdlr2_payload += b'\x00' * 12 + b'\x00'
    hdlr2_box = build_fullbox(b'hdlr', 0, 0, hdlr2_payload)

    name_payload = cfg.get("name_box_payload", "").encode('utf-8')
    name_box = build_box(b'name', name_payload) if name_payload else b''

    ilst2 = b''
    short_comment = cfg.get("comment_short", "")
    if short_comment:
        ilst2 += build_ilst_entry('\xa9cmt'.encode('latin-1'), short_comment)
    ilst2_box = build_box(b'ilst', ilst2)

    meta2_inner = hdlr2_box + name_box + ilst2_box
    meta2_payload = b'\x00\x00\x00\x00' + meta2_inner
    meta2_box = build_fullbox(b'meta', 0, 0, meta2_payload)

    udta_payload = meta1_box + meta2_box
    return build_box(b'udta', udta_payload)


def build_trailing_garbage(size: int) -> bytes:
    if size <= 0:
        return b''
    void_box = b'\x00\x00\x00\x04VOID'
    pattern = b'\x00\x00\x00\x04'
    remaining = size - len(void_box)
    repeats = max(0, remaining // len(pattern))
    return void_box + (pattern * repeats)


def detect_video_codec(data: bytearray) -> bytes:
    """detect video codec fourcc from first video sample entry in stsd"""
    moov = find_box(data, b'moov')
    if not moov:
        return b'avc1'
    for trak in parse_boxes(data, moov["offset"] + 8, moov["end"]):
        if trak["type"] != b'trak':
            continue
        for mdia in parse_boxes(data, trak["offset"] + 8, trak["end"]):
            if mdia["type"] != b'mdia':
                continue
            is_video = False
            for child in parse_boxes(data, mdia["offset"] + 8, mdia["end"]):
                if child["type"] == b'hdlr':
                    ht = data[child["offset"]+16:child["offset"]+20]
                    if ht == b'vide':
                        is_video = True
                    break
            if not is_video:
                continue
            for minf in parse_boxes(data, mdia["offset"] + 8, mdia["end"]):
                if minf["type"] != b'minf':
                    continue
                for stbl in parse_boxes(data, minf["offset"] + 8, minf["end"]):
                    if stbl["type"] != b'stbl':
                        continue
                    for stsd in parse_boxes(data, stbl["offset"] + 8, stbl["end"]):
                        if stsd["type"] != b'stsd':
                            continue
                        entries = parse_boxes(data, stsd["offset"] + 8, stsd["end"])
                        if entries:
                            return entries[0]["type"]
    return b'avc1'


def zero_mp4a_samplerate(data: bytearray):
    top_boxes = parse_boxes(data, 0, len(data))
    moov = next((b for b in top_boxes if b["type"] == b'moov'), None)
    if not moov:
        return
    _zero_mp4a_recursive(data, moov["offset"] + 8, moov["end"])


def _zero_mp4a_recursive(data: bytearray, start: int, end: int):
    pos = start
    while pos + 8 <= end:
        sz = read_u32be(data, pos)
        if sz < 8 or pos + sz > end:
            break
        typ = data[pos+4:pos+8]
        if typ == b'mp4a' and sz >= 36:
            write_u32be(data, pos + 28, 0)
        elif typ in (b'moov', b'trak', b'mdia', b'minf', b'stbl', b'stsd'):
            _zero_mp4a_recursive(data, pos + 8, pos + sz)
        pos += sz


def spoof_audio_bitrate(data: bytearray, source_path: str):
    """copy esds avgBitrate/maxBitrate and mp4a-btrt from source to all target mp4a entries"""
    try:
        with open(source_path, 'rb') as f:
            src = bytearray(f.read())
    except IOError:
        return

    src_moov = find_box(src, b'moov')
    if not src_moov:
        return

    src_bitrates = []
    _extract_mp4a_bitrates(src, src_moov["offset"] + 8, src_moov["end"], src_bitrates)
    if not src_bitrates:
        return
    src_avg, src_max = src_bitrates[0]

    tgt_moov = find_box(data, b'moov')
    if not tgt_moov:
        return
    _apply_mp4a_bitrates(data, tgt_moov["offset"] + 8, tgt_moov["end"], src_avg, src_max)


def _extract_mp4a_bitrates(data: bytearray, start: int, end: int, results: list):
    pos = start
    while pos + 8 <= end:
        sz = read_u32be(data, pos)
        if sz < 8 or pos + sz > end:
            break
        typ = data[pos+4:pos+8]
        if typ == b'mp4a' and sz >= 36:
            cpos = pos + 36
            while cpos + 8 <= pos + sz:
                csz = read_u32be(data, cpos)
                if csz < 8 or cpos + csz > pos + sz:
                    break
                ctyp = data[cpos+4:cpos+8]
                if ctyp == b'esds' and csz >= 30:
                    max_br = read_u32be(data, cpos + 22)
                    avg_br = read_u32be(data, cpos + 26)
                    results.append((avg_br, max_br))
                cpos += csz
        elif typ in (b'moov', b'trak', b'mdia', b'minf', b'stbl', b'stsd'):
            _extract_mp4a_bitrates(data, pos + 8, pos + sz, results)
        pos += sz


def _apply_mp4a_bitrates(data: bytearray, start: int, end: int, avg: int, mx: int) -> int:
    count = 0
    pos = start
    while pos + 8 <= end:
        sz = read_u32be(data, pos)
        if sz < 8 or pos + sz > end:
            break
        typ = data[pos+4:pos+8]
        if typ == b'mp4a' and sz >= 36:
            cpos = pos + 36
            while cpos + 8 <= pos + sz:
                csz = read_u32be(data, cpos)
                if csz < 8 or cpos + csz > pos + sz:
                    break
                ctyp = data[cpos+4:cpos+8]
                if ctyp == b'esds' and csz >= 30:
                    write_u32be(data, cpos + 22, mx)
                    write_u32be(data, cpos + 26, avg)
                    count += 1
                elif ctyp == b'btrt' and csz >= 20:
                    write_u32be(data, cpos + 12, avg)
                    write_u32be(data, cpos + 16, mx)
                cpos += csz
        elif typ in (b'moov', b'trak', b'mdia', b'minf', b'stbl', b'stsd'):
            count += _apply_mp4a_bitrates(data, pos + 8, pos + sz, avg, mx)
        pos += sz
    return count


def copy_avcc_from_source(source_path: str, target_data: bytearray) -> bytearray:
    try:
        with open(source_path, 'rb') as f:
            src = bytearray(f.read())
    except IOError:
        return target_data

    src_moov = find_box(src, b'moov')
    tgt_moov = find_box(target_data, b'moov')
    if not src_moov or not tgt_moov:
        return target_data

    for cfg_box in (b'avcC', b'hvcC'):
        src_cfg = find_box(src, cfg_box, src_moov["offset"], src_moov["end"])
        tgt_cfg = find_box(target_data, cfg_box, tgt_moov["offset"], tgt_moov["end"])
        if src_cfg and tgt_cfg and src_cfg["size"] == tgt_cfg["size"]:
            target_data[tgt_cfg["offset"]:tgt_cfg["end"]] = src[src_cfg["offset"]:src_cfg["end"]]

    return target_data


def fix_btrt_from_source(source_path: str, target_data: bytearray):
    try:
        with open(source_path, 'rb') as f:
            src = bytearray(f.read())
    except IOError:
        return

    src_moov = find_box(src, b'moov')
    tgt_moov = find_box(target_data, b'moov')
    if not src_moov or not tgt_moov:
        return

    src_btrt = find_box(src, b'btrt', src_moov["offset"], src_moov["end"])
    tgt_btrt = find_box(target_data, b'btrt', tgt_moov["offset"], tgt_moov["end"])

    if src_btrt and tgt_btrt and src_btrt["size"] == tgt_btrt["size"]:
        avg = read_u32be(src, src_btrt["offset"] + 12)
        write_u32be(target_data, tgt_btrt["offset"] + 12, avg)


def strip_free_boxes(data: bytearray) -> bytearray:
    top_boxes = parse_boxes(data, 0, len(data))
    keep_ranges = []
    for box in top_boxes:
        if box["type"] not in (b'free', b'skip'):
            keep_ranges.append((box["offset"], box["end"]))
    if len(keep_ranges) == len(top_boxes):
        return data
    result = bytearray()
    for start, end in keep_ranges:
        result.extend(data[start:end])
    return result


def upgrade_mvhd_to_v1(mvhd: bytearray) -> bytearray:
    """convert mvhd to version 1 with unknown duration and nexttrackid=5"""
    ver = mvhd[8]
    timescale = read_u32be(mvhd, 20) if ver == 0 else read_u32be(mvhd, 28)
    ntid_off = 96 if ver == 0 else 108
    ntid = read_u32be(mvhd, ntid_off)

    body = bytearray(112)
    body[0] = 1
    struct.pack_into('>I', body, 20, timescale)
    struct.pack_into('>Q', body, 24, 0xFFFFFFFFFFFFFFFF)
    struct.pack_into('>I', body, 32, 0x00010000)
    struct.pack_into('>H', body, 36, 0x0100)
    struct.pack_into('>I', body, 44, 0x00010000)
    struct.pack_into('>I', body, 60, 0x00010000)
    struct.pack_into('>I', body, 76, 0x40000000)
    struct.pack_into('>I', body, 108, 5)

    return bytearray(build_box(b'mvhd', bytes(body)))


def patch_elst_plus_one(edts_data: bytearray) -> bytearray:
    """increment first elst segment_duration by 1 tick"""
    children = parse_boxes(edts_data, 8, len(edts_data))
    new_children = []
    for c in children:
        if c["type"] == b'elst':
            elst = bytearray(edts_data[c["offset"]:c["end"]])
            ver = elst[8]
            count = read_u32be(elst, 12)
            if count > 0:
                dur_off = 16 if ver == 0 else 20
                dur = read_u32be(elst, dur_off)
                write_u32be(elst, dur_off, dur + 1)
            new_children.append(bytes(elst))
        else:
            new_children.append(bytes(edts_data[c["offset"]:c["end"]]))
    return bytearray(build_box(b'edts', b''.join(new_children)))


def patch_video(input_path: str, config: dict = None) -> bool:
    cfg = get_config(config)
    p = Path(input_path)
    output_path = str(p.parent / f"{p.stem}_tiktok.mp4")

    if cfg.get("method", "mvhd") == "mvhd" and p.suffix.lower() == ".mp4":
        shutil.copy2(input_path, output_path)
        if not patch_mp4(output_path, cfg, input_path):
            raise RuntimeError("Patching MP4 structure failed")
        return True

    if cfg["re_encode"]:
        if not encode_for_tiktok(input_path, output_path, cfg.get("crf", 18), cfg.get("codec", "h264")):
            raise RuntimeError("Encoding failed during execution")
    else:
        if not remux_for_tiktok(input_path, output_path):
            raise RuntimeError("Remux failed during execution")

    if not patch_mp4(output_path, cfg, input_path):
        raise RuntimeError("Patching MP4 structure failed")

    return True



WATERMARK = "Patched by https://github.com/buwryme/tiktok-lossless-upload"


def _cb_u32(data, offset):
    return struct.unpack('>I', data[offset:offset + 4])[0]


def _cb_u64(data, offset):
    return struct.unpack('>Q', data[offset:offset + 8])[0]


def _cb_box(box_type, payload):
    return struct.pack('>I', 8 + len(payload)) + box_type + payload


def _cb_fullbox(box_type, version, flags, payload):
    return _cb_box(
        box_type,
        struct.pack('>I', (version << 24) | (flags & 0x00FFFFFF)) + payload,
    )


def _cb_boxes(data, start=0, end=None):
    end = len(data) if end is None else end
    pos = start
    while pos + 8 <= end:
        size = _cb_u32(data, pos)
        box_type = data[pos + 4:pos + 8]
        header = 8
        if size == 1:
            size = _cb_u64(data, pos + 8)
            header = 16
        elif size == 0:
            size = end - pos
        if size < 8 or pos + size > end:
            break
        yield {
            "offset": pos,
            "size": size,
            "type": box_type,
            "header": header,
            "end": pos + size,
            "payload": data[pos + header:pos + size],
        }
        pos += size


def _cb_upgrade_mvhd(mvhd):
    body = mvhd[8:]
    version = body[0]
    if version == 1:
        updated = bytearray(body)
        updated[24:32] = b"\xff" * 8
        return _cb_box(b"mvhd", bytes(updated))
    if version != 0 or len(body) < 100:
        return mvhd
    updated = bytearray(112)
    updated[0:4] = struct.pack(">I", 1 << 24)
    updated[4:12] = struct.pack(">Q", _cb_u32(body, 4))
    updated[12:20] = struct.pack(">Q", _cb_u32(body, 8))
    updated[20:24] = body[12:16]
    updated[24:32] = b"\xff" * 8
    updated[32:36] = body[20:24]
    updated[36:38] = body[24:26]
    updated[38:48] = body[26:36]
    updated[48:84] = body[36:72]
    updated[84:108] = body[72:96]
    updated[108:112] = body[96:100]
    return _cb_box(b"mvhd", bytes(updated))


def _cb_comment_entry(value=WATERMARK):
    data = _cb_box(
        b"data",
        struct.pack(">II", 1, 0) + value.encode("utf-8"),
    )
    return _cb_box(b"\xa9cmt", data)


def _cb_patch_udta(udta):
    children = list(_cb_boxes(udta, 8, len(udta)))
    output = []
    has_meta = False
    for child in children:
        if child["type"] != b"meta":
            output.append(udta[child["offset"]:child["end"]])
            continue
        has_meta = True
        meta_payload = udta[child["offset"] + 8:child["end"]]
        meta_children = list(_cb_boxes(meta_payload, 4, len(meta_payload)))
        new_meta = []
        has_ilst = False
        for meta_child in meta_children:
            if meta_child["type"] != b"ilst":
                new_meta.append(meta_payload[meta_child["offset"]:meta_child["end"]])
                continue
            has_ilst = True
            ilst_payload = meta_payload[meta_child["offset"] + 8:meta_child["end"]]
            entries = list(_cb_boxes(ilst_payload, 0, len(ilst_payload)))
            new_entries = []
            has_comment = False
            for entry in entries:
                if entry["type"] == b"\xa9cmt":
                    new_entries.append(_cb_comment_entry())
                    has_comment = True
                else:
                    new_entries.append(ilst_payload[entry["offset"]:entry["end"]])
            if not has_comment:
                new_entries.append(_cb_comment_entry())
            new_meta.append(_cb_box(b"ilst", b"".join(new_entries)))
        if not has_ilst:
            new_meta.append(_cb_box(b"ilst", _cb_comment_entry()))
        output.append(_cb_fullbox(b"meta", 0, 0, b"".join(new_meta)))
    if not has_meta:
        hdlr = _cb_fullbox(
            b"hdlr", 0, 0,
            struct.pack(">I", 0) + b"mdir" + b"\x00" * 12 + b"\x00",
        )
        output.append(_cb_fullbox(
            b"meta", 0, 0,
            hdlr + _cb_box(b"ilst", _cb_comment_entry()),
        ))
    return _cb_box(b"udta", b"".join(output))


def _cb_shift_offsets(data, start, end, delta):
    for box in _cb_boxes(data, start, end):
        if box["type"] in (b"stco", b"co64"):
            count = _cb_u32(data, box["offset"] + 12)
            is64 = box["type"] == b"co64"
            for index in range(count):
                offset = box["offset"] + 16 + index * (8 if is64 else 4)
                value = _cb_u64(data, offset) if is64 else _cb_u32(data, offset)
                value += delta
                if is64:
                    data[offset:offset + 8] = struct.pack(">Q", value)
                else:
                    data[offset:offset + 4] = struct.pack(">I", value & 0xFFFFFFFF)
        elif box["type"] in (b"moov", b"trak", b"mdia", b"minf", b"stbl"):
            _cb_shift_offsets(data, box["offset"] + 8, box["end"], delta)


def _cb_rebuild(data, delta=0):
    output = []
    for box in _cb_boxes(data, 0, len(data)):
        raw = data[box["offset"]:box["end"]]
        if box["type"] == b"moov":
            moov_children = []
            for child in _cb_boxes(raw, 8, len(raw)):
                child_raw = raw[child["offset"]:child["end"]]
                if child["type"] == b"mvhd":
                    moov_children.append(_cb_upgrade_mvhd(child_raw))
                elif child["type"] == b"udta":
                    moov_children.append(_cb_patch_udta(child_raw))
                else:
                    moov_children.append(child_raw)
            moov = _cb_box(b"moov", b"".join(moov_children))
            if delta:
                mutable = bytearray(moov)
                _cb_shift_offsets(mutable, 8, len(mutable), delta)
                moov = bytes(mutable)
            output.append(moov)
        else:
            output.append(raw)
    return b"".join(output)


def patch_bytes_mvhd(data):
    old_moov = next(b for b in _cb_boxes(data, 0, len(data)) if b["type"] == b"moov")
    old_mdat = next(b for b in _cb_boxes(data, 0, len(data)) if b["type"] == b"mdat")
    probe = _cb_rebuild(data, 0)
    new_moov = next(b for b in _cb_boxes(probe, 0, len(probe)) if b["type"] == b"moov")
    delta = new_moov["size"] - old_moov["size"]
    if old_moov["offset"] > old_mdat["offset"]:
        delta = 0
    return _cb_rebuild(data, delta)


def patch_mp4_mvhd(input_path, cfg=None):
    with open(input_path, "rb") as handle:
        data = handle.read()
    with open(input_path, "wb") as handle:
        handle.write(patch_bytes_mvhd(data))
    return True


def patch_mp4(input_path: str, cfg: dict, source_path: str = None) -> bool:
    if cfg.get("method", "mvhd") == "mvhd":
        return patch_mp4_mvhd(input_path, cfg)
    try:
        with open(input_path, 'rb') as f:
            raw = f.read()
    except IOError as e:
        print(f"       couldn't read {input_path}: {e}")
        return False

    data = bytearray(raw)
    orig_size = len(data)
    print(f"       \033[1minput\033[0m: {input_path}")
    print(f"       \033[1msize\033[0m: {orig_size:,} bytes")

    if source_path and not cfg.get("re_encode", True):
        data = copy_avcc_from_source(source_path, data)

    # detect video codec and set appropriate dummy size
    video_codec = detect_video_codec(data)
    global STSZ_INFLATE_FACTOR, DUMMY_SAMPLE_SIZE
    STSZ_INFLATE_FACTOR = cfg.get("inflation_rate", DEFAULTS["inflation_rate"])
    DUMMY_SAMPLE_SIZE = CODEC_DUMMY_SIZES.get(video_codec, 8)
    print(f"       \033[1mcodec\033[0m: {video_codec.decode('ascii', errors='replace')} (dummy={DUMMY_SAMPLE_SIZE})")

    print("       \033[1m[1/7]\033[0m parsing box structure...")
    top_boxes = parse_boxes(data, 0, len(data))

    ftyp_box = moov_box = mdat_box = None
    for box in top_boxes:
        if box["type"] == b'ftyp': ftyp_box = box
        elif box["type"] == b'moov': moov_box = box
        elif box["type"] == b'mdat': mdat_box = box

    if not moov_box or not mdat_box:
        print("       ERROR: missing moov or mdat, aborting")
        return False

    print("       \033[1m[2/7]\033[0m reconstructing layout (ftyp → moov → mdat)...")
    ftyp_data = data[ftyp_box["offset"]:ftyp_box["end"]] if ftyp_box else b''
    moov_data = bytearray(data[moov_box["offset"]:moov_box["end"]])

    mdat_header_size = 16 if read_u32be(data, mdat_box["offset"]) == 1 else 8
    mdat_payload = data[mdat_box["offset"] + mdat_header_size:mdat_box["end"]]
    mdat_data = build_box(b'mdat', mdat_payload)

    print("       \033[1m[3/7]\033[0m patching moov...")
    moov_children = parse_boxes(moov_data, 8, len(moov_data))

    video_trak_idx = audio_trak_idx = tmcd_trak_idx = None
    for i, child in enumerate(moov_children):
        if child["type"] == b'trak':
            trak_children = parse_boxes(moov_data, child["offset"] + 8, child["end"])
            for tc in trak_children:
                if tc["type"] == b'mdia':
                    mdia_children = parse_boxes(moov_data, tc["offset"] + 8, tc["end"])
                    for mc in mdia_children:
                        if mc["type"] == b'hdlr':
                            ht = moov_data[mc["offset"]+16:mc["offset"]+20]
                            if ht == b'vide': video_trak_idx = i
                            elif ht == b'soun': audio_trak_idx = i
                            elif ht == b'tmcd': tmcd_trak_idx = i

    new_moov_children = []
    for i, child in enumerate(moov_children):
        if child["type"] == b'mvhd':
            mvhd = bytearray(moov_data[child["offset"]:child["end"]])
            mvhd = upgrade_mvhd_to_v1(mvhd)
            new_moov_children.append(bytes(mvhd))
        elif child["type"] == b'trak':
            trak_data = bytearray(moov_data[child["offset"]:child["end"]])
            is_tmcd = (i == tmcd_trak_idx)
            is_audio = (i == audio_trak_idx)
            is_video = (i == video_trak_idx)
            if is_audio:
                primary = patch_trak(trak_data, is_video, True, inflate=False, track_id=2, is_clone=False)
                new_moov_children.append(bytes(primary))
                clone = bytearray(trak_data)
                clone_patched = patch_trak(clone, False, True, inflate=True, track_id=4, is_clone=True)
                new_moov_children.append(bytes(clone_patched))
            elif is_tmcd:
                patched = patch_trak(trak_data, False, False, inflate=False, track_id=3, is_clone=False)
                new_moov_children.append(bytes(patched))
            else:
                patched = patch_trak(trak_data, is_video, False, inflate=False, track_id=1, is_clone=False)
                new_moov_children.append(bytes(patched))
        elif child["type"] == b'udta':
            continue
        else:
            new_moov_children.append(bytes(moov_data[child["offset"]:child["end"]]))

    combined_udta = build_combined_udta(cfg)
    new_moov_children.append(combined_udta)

    moov_payload = b''.join(new_moov_children)
    new_moov = build_box(b'moov', moov_payload)

    print("       \033[1m[4/7]\033[0m assembling output...")
    output_data = bytearray(ftyp_data) + bytearray(new_moov) + mdat_data

    print("       \033[1m[5/7]\033[0m fixing chunk offsets...")
    new_mdat_offset = len(ftyp_data) + len(new_moov) + 8
    old_mdat_payload_offset = mdat_box["offset"] + mdat_header_size
    offset_delta = new_mdat_offset - old_mdat_payload_offset
    if offset_delta != 0:
        fix_chunk_offsets(output_data, offset_delta)

    print("       \033[1m[6/7]\033[0m post-patch fixes...")
    zero_mp4a_samplerate(output_data)
    if source_path:
        spoof_audio_bitrate(output_data, source_path)
        fix_btrt_from_source(source_path, output_data)
    output_data = strip_free_boxes(output_data)

    print("       \033[1m[7/7]\033[0m appending trailing data...")
    garbage = build_trailing_garbage(cfg["trailing_bytes"])
    output_data += garbage

    try:
        with open(input_path, 'wb') as f:
            f.write(bytes(output_data))
    except IOError as e:
        print(f"       couldn't write to {input_path}: {e}")
        return False

    final_size = len(output_data)
    print(f"       \033[1mdone!\033[0m ({orig_size:,} bytes in, {final_size:,} bytes out)")
    return True


def patch_trak(trak_data: bytearray, is_video: bool, is_audio: bool,
               inflate: bool = False, track_id: int = 0, is_clone: bool = False) -> bytearray:
    trak_children = parse_boxes(trak_data, 8, len(trak_data))
    new_children = []

    for tc in trak_children:
        if tc["type"] == b'tkhd':
            tkhd = bytearray(trak_data[tc["offset"]:tc["end"]])
            ver = tkhd[8]
            if ver == 0:
                write_u32be(tkhd, 12, 0)
                write_u32be(tkhd, 16, 0)
            else:
                write_u64be(tkhd, 12, 0)
                write_u64be(tkhd, 20, 0)
            if track_id > 0:
                tid_off = 20 if ver == 0 else 28
                write_u32be(tkhd, tid_off, track_id)
            new_children.append(bytes(tkhd))
        elif tc["type"] == b'tref':
            if is_clone:
                continue
            new_children.append(bytes(trak_data[tc["offset"]:tc["end"]]))
        elif tc["type"] == b'edts':
            if is_clone:
                continue
            if is_video:
                edts_data = bytearray(trak_data[tc["offset"]:tc["end"]])
                edts_data = patch_elst_plus_one(edts_data)
                new_children.append(bytes(edts_data))
            else:
                new_children.append(bytes(trak_data[tc["offset"]:tc["end"]]))
        elif tc["type"] == b'mdia':
            mdia_data = bytearray(trak_data[tc["offset"]:tc["end"]])
            mdia_data = patch_mdia(mdia_data, is_video, is_audio, inflate)
            new_children.append(bytes(mdia_data))
        else:
            new_children.append(bytes(trak_data[tc["offset"]:tc["end"]]))

    return bytearray(build_box(b'trak', b''.join(new_children)))


def patch_mdia(mdia_data: bytearray, is_video: bool, is_audio: bool,
               inflate: bool = False) -> bytearray:
    mdia_children = parse_boxes(mdia_data, 8, len(mdia_data))
    new_children = []

    for mc in mdia_children:
        if mc["type"] == b'mdhd':
            mdhd = bytearray(mdia_data[mc["offset"]:mc["end"]])
            ver = mdhd[8]
            if ver == 0:
                write_u32be(mdhd, 12, 0)
                write_u32be(mdhd, 16, 0)
            else:
                write_u64be(mdhd, 12, 0)
                write_u64be(mdhd, 20, 0)
            new_children.append(bytes(mdhd))
        elif mc["type"] == b'hdlr':
            if is_video:
                new_children.append(build_hdlr(b'vide', "VideoHandler"))
            elif is_audio:
                new_children.append(build_hdlr(b'soun', "SoundHandler"))
            else:
                new_children.append(build_hdlr(b'vide', ""))
        elif mc["type"] == b'minf':
            minf_data = bytearray(mdia_data[mc["offset"]:mc["end"]])
            minf_data = patch_minf(minf_data, is_audio, inflate)
            new_children.append(bytes(minf_data))
        else:
            new_children.append(bytes(mdia_data[mc["offset"]:mc["end"]]))

    return bytearray(build_box(b'mdia', b''.join(new_children)))


def patch_minf(minf_data: bytearray, is_audio: bool, inflate: bool = False) -> bytearray:
    minf_children = parse_boxes(minf_data, 8, len(minf_data))
    new_children = []

    for mc in minf_children:
        if mc["type"] == b'nmhd':
            continue
        elif mc["type"] == b'stbl' and is_audio and inflate:
            stbl_data = bytearray(minf_data[mc["offset"]:mc["end"]])
            stbl_data = patch_stbl(stbl_data)
            new_children.append(bytes(stbl_data))
        else:
            new_children.append(bytes(minf_data[mc["offset"]:mc["end"]]))

    return bytearray(build_box(b'minf', b''.join(new_children)))


def patch_stbl(stbl_data: bytearray) -> bytearray:
    global STSZ_INFLATE_FACTOR, DUMMY_SAMPLE_SIZE
    stbl_children = parse_boxes(stbl_data, 8, len(stbl_data))

    stsz_box = stts_box = stsc_box = stco_box = None
    for sc in stbl_children:
        if sc["type"] == b'stsz': stsz_box = sc
        elif sc["type"] == b'stts': stts_box = sc
        elif sc["type"] == b'stsc': stsc_box = sc
        elif sc["type"] == b'stco': stco_box = sc

    if not all([stsz_box, stts_box, stsc_box, stco_box]):
        return stbl_data

    inflate_factor = STSZ_INFLATE_FACTOR
    dummy_size = DUMMY_SAMPLE_SIZE

    stsz_payload = stbl_data[stsz_box["offset"] + 12:stsz_box["end"]]
    if len(stsz_payload) < 8:
        return stbl_data

    uniform_size = read_u32be(stsz_payload, 0)
    sample_count = read_u32be(stsz_payload, 4)

    original_sizes = []
    if uniform_size == 0:
        pos = 8
        for _ in range(sample_count):
            if pos + 4 > len(stsz_payload): break
            original_sizes.append(read_u32be(stsz_payload, pos))
            pos += 4
    else:
        original_sizes = [uniform_size] * sample_count

    if not original_sizes:
        return stbl_data

    real_count = len(original_sizes)
    extra_count = real_count * (inflate_factor - 1)

    stsc_payload = stbl_data[stsc_box["offset"] + 12:stsc_box["end"]]
    if len(stsc_payload) < 4:
        return stbl_data
    stsc_entry_count = read_u32be(stsc_payload, 0)
    stsc_entries = []
    pos = 4
    for _ in range(stsc_entry_count):
        if pos + 12 > len(stsc_payload): break
        fc = read_u32be(stsc_payload, pos)
        spc = read_u32be(stsc_payload, pos + 4)
        sdi = read_u32be(stsc_payload, pos + 8)
        stsc_entries.append((fc, spc, sdi))
        pos += 12
    last_sdi = stsc_entries[-1][2] if stsc_entries else 1

    stco_payload = stbl_data[stco_box["offset"] + 12:stco_box["end"]]
    if len(stco_payload) < 4:
        return stbl_data
    stco_entry_count = read_u32be(stco_payload, 0)
    stco_offsets = []
    pos = 4
    for _ in range(stco_entry_count):
        if pos + 4 > len(stco_payload): break
        stco_offsets.append(read_u32be(stco_payload, pos))
        pos += 4
    if not stco_offsets:
        return stbl_data

    def stsc_total(entries, chunks):
        total = 0
        for i, e in enumerate(entries):
            fc, spc, _ = e
            nfc = entries[i+1][0] if i+1 < len(entries) else chunks + 1
            if nfc > fc:
                total += (nfc - fc) * spc
        return total

    orig_chunks = len(stco_offsets)
    if not stsc_entries or stsc_total(stsc_entries, orig_chunks) != real_count:
        stsc_entries = [(1, real_count, last_sdi)]
        stco_offsets = [stco_offsets[0]]
        orig_chunks = 1

    first_dummy_offset = stco_offsets[0]

    new_sizes = list(original_sizes) + [dummy_size] * extra_count
    new_count = len(new_sizes)
    new_stsz_payload = struct.pack('>II', 0, new_count)
    for sz in new_sizes:
        new_stsz_payload += struct.pack('>I', sz)
    new_stsz = build_fullbox(b'stsz', 0, 0, new_stsz_payload)
    print(f"       \033[1mstsz\033[0m (clone): {real_count} → {new_count} (dummy={dummy_size})")

    orig_stts_pay = stbl_data[stts_box["offset"] + 12:stts_box["end"]]
    orig_tc = read_u32be(orig_stts_pay, 0) if len(orig_stts_pay) >= 4 else 0
    ext_payload = struct.pack('>I', orig_tc + 1) + orig_stts_pay[4:]
    ext_payload += struct.pack('>II', extra_count, 1)
    new_stts = build_fullbox(b'stts', 0, 0, ext_payload)

    new_stsc_entries = list(stsc_entries)
    if extra_count > 0:
        new_stsc_entries.append((orig_chunks + 1, extra_count, last_sdi))
    new_stsc_payload = struct.pack('>I', len(new_stsc_entries))
    for fc, spc, sdi in new_stsc_entries:
        new_stsc_payload += struct.pack('>III', fc, spc, sdi)
    new_stsc = build_fullbox(b'stsc', 0, 0, new_stsc_payload)

    new_stco_offsets = list(stco_offsets)
    if extra_count > 0:
        new_stco_offsets.append(first_dummy_offset)
    new_stco_payload = struct.pack('>I', len(new_stco_offsets))
    for off in new_stco_offsets:
        new_stco_payload += struct.pack('>I', off)
    new_stco = build_fullbox(b'stco', 0, 0, new_stco_payload)

    new_children = []
    for sc in stbl_children:
        if sc["type"] == b'stsz': new_children.append(new_stsz)
        elif sc["type"] == b'stts': new_children.append(new_stts)
        elif sc["type"] == b'stsc': new_children.append(new_stsc)
        elif sc["type"] == b'stco': new_children.append(new_stco)
        else: new_children.append(bytes(stbl_data[sc["offset"]:sc["end"]]))

    return bytearray(build_box(b'stbl', b''.join(new_children)))


def fix_chunk_offsets(data: bytearray, delta: int):
    top_boxes = parse_boxes(data, 0, len(data))
    for box in top_boxes:
        if box["type"] == b'moov':
            fix_offsets_recursive(data, box["offset"] + 8, box["end"], delta)


def fix_offsets_recursive(data: bytearray, start: int, end: int, delta: int):
    pos = start
    while pos + 8 <= end:
        sz = read_u32be(data, pos)
        if sz < 8 or pos + sz > end: break
        typ = data[pos+4:pos+8]
        if typ == b'stco':
            cnt = read_u32be(data, pos + 12)
            for i in range(cnt):
                v = read_u32be(data, pos + 16 + i*4)
                if v > 0: write_u32be(data, pos + 16 + i*4, v + delta)
        elif typ == b'co64':
            cnt = read_u32be(data, pos + 12)
            for i in range(cnt):
                hi = read_u32be(data, pos + 16 + i*8)
                lo = read_u32be(data, pos + 20 + i*8)
                v = (hi << 32) + lo
                if v > 0:
                    v += delta
                    write_u32be(data, pos + 16 + i*8, (v >> 32) & 0xFFFFFFFF)
                    write_u32be(data, pos + 20 + i*8, v & 0xFFFFFFFF)
        elif typ in (b'moov', b'trak', b'mdia', b'minf', b'stbl'):
            fix_offsets_recursive(data, pos + 8, pos + sz, delta)
        pos += sz


def encode_for_tiktok(input_path: str, output_path: str, crf: int = 18, codec: str = "h264") -> bool:
    if codec == "h264":
        video_args = [
            "-c:v", "libx264", "-preset", "medium",
            "-crf", str(crf), "-level", "4.2",
            "-pix_fmt", "yuv420p",
            "-g", "9999", "-bf", "0",
            "-force_key_frames", "0,9.1,18.2",
        ]
    else:
        video_args = [
            "-c:v", "libx265", "-preset", "medium",
            "-crf", str(crf), "-pix_fmt", "yuv420p10le",
            "-x265-params", "log-level=error",
            "-g", "9999", "-bf", "0",
        ]

    cmd = [
        "ffmpeg", "-i", input_path,
        *video_args,
        "-c:a", "aac", "-b:a", "256k",
        "-movflags", "+faststart",
        "-metadata:s:v", "handler_name=VideoHandler",
        "-metadata:s:a", "handler_name=SoundHandler",
        "-map_metadata:s:v", "0:s:v",
        "-map_metadata:s:a", "0:s:a",
        "-y", output_path
    ]

    print(f"       \033[3m{codec} crf{crf} yuv420p level4.2\033[0m\n")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        err = result.stderr[-800:] if len(result.stderr) > 800 else result.stderr
        print(f"       ✗ {err}")
        return False
    if os.path.exists(output_path):
        mb = os.path.getsize(output_path) / (1024 * 1024)
        print(f"       ✓ \033[1mencoded\033[0m ({mb:.1f} MB)")
        return True
    print(f"       ✗ output not created")
    return False


def remux_for_tiktok(input_path: str, output_path: str) -> bool:
    cmd = [
        "ffmpeg", "-i", input_path,
        "-c:v", "copy", "-c:a", "copy",
        "-movflags", "+faststart",
        "-metadata:s:v", "handler_name=VideoHandler",
        "-metadata:s:a", "handler_name=SoundHandler",
        "-map_metadata:s:v", "0:s:v",
        "-map_metadata:s:a", "0:s:a",
        "-avoid_negative_ts", "disabled",
        "-copyinkf",
        "-y", output_path
    ]
    print(f"       \033[3mremux passthrough\033[0m\n")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        err = result.stderr[-800:] if len(result.stderr) > 800 else result.stderr
        print(f"       ✗ {err}")
        return False
    if os.path.exists(output_path):
        mb = os.path.getsize(output_path) / (1024 * 1024)
        print(f"       ✓ \033[1mremuxed\033[0m ({mb:.1f} MB)")
        return True
    print(f"       ✗ output not created")
    return False


STSZ_INFLATE_FACTOR = DEFAULTS["inflation_rate"]
DUMMY_SAMPLE_SIZE = DEFAULTS["dummy_sample_size"]


def main():
    if len(sys.argv) < 2:
        print("usage:")
        print(f"  python3 {sys.argv[0]} input_file [output.mp4]")
        print()
        print("examples:")
        print(f'  python3 {sys.argv[0]} in.mp4 out.mp4')
        print(f'  python3 -c "from tiktok_patch import patch_video; patch_video(\'in.mp4\', {{\'name_box_payload\': \'my-custom-tag\', \'comment\': \'custom\'}})"')
        print()
        sys.exit(1)

    input_file = sys.argv[1]
    output_file = sys.argv[2] if len(sys.argv) > 2 else None

    if not os.path.exists(input_file):
        print(f"error: '{input_file}' not found")
        sys.exit(1)

    if not output_file:
        stem = Path(input_file).stem
        output_file = f"{stem}_tiktok.mp4"

    print()
    print(f"  \033[1mTikTok Optimization Pipeline\033[0m")
    print()
    print(f"  \033[1minput\033[0m:  {input_file}")
    print(f"  \033[1moutput\033[0m: {output_file}")
    print()

    print(f"\033[1m[1/2] Encoding...\033[0m\n")

    cfg = get_config()
    if not encode_for_tiktok(input_file, output_file, cfg.get("crf", 18), cfg.get("codec", "h264")):
        print("\n  ✗ encoding failed")
        sys.exit(1)

    print(f"\n\033[1m[2/2] Patching...\033[0m\n")

    global STSZ_INFLATE_FACTOR, DUMMY_SAMPLE_SIZE
    STSZ_INFLATE_FACTOR = cfg["inflation_rate"]
    DUMMY_SAMPLE_SIZE = cfg["dummy_sample_size"]

    if patch_mp4(output_file, cfg, input_file):
        print(f"\n  ✓ \033[1mdone\033[0m: {output_file}")
        return 0
    else:
        print(f"\n  ✗ patch failed (file may still work)")
        return 1


if __name__ == '__main__':
    sys.exit(main())
