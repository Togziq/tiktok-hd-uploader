"""Video preparation pipeline: probe -> (copy | remux | encode) -> mvhd patch."""

import glob
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import tiktok_lossless_patch as patcher

NO_WINDOW = 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW
HDR_TRANSFERS = {"smpte2084", "arib-std-b67"}
PASSTHROUGH_CODECS = {"h264", "hevc"}
VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi"}

MODE_LABELS = {
    "patch": "без перекодировки",
    "remux": "перепаковка",
    "encode": "перекодировка",
}


def app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).parent


def find_tool(name: str) -> str | None:
    exe = name + ".exe" if os.name == "nt" else name
    local = app_dir() / exe
    if local.exists():
        return str(local)
    found = shutil.which(name)
    if found:
        return found
    pattern = os.path.join(
        os.environ.get("LOCALAPPDATA", ""), "Microsoft", "WinGet", "Packages",
        "Gyan.FFmpeg*", "*", "bin", exe,
    )
    hits = sorted(glob.glob(pattern))
    return hits[-1] if hits else None


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8",
        errors="replace", creationflags=NO_WINDOW,
    )


def probe(path: str) -> dict | None:
    ffprobe = find_tool("ffprobe")
    if not ffprobe:
        return None
    result = _run([
        ffprobe, "-v", "error", "-select_streams", "v:0",
        "-show_entries",
        "stream=codec_name,width,height,pix_fmt,color_transfer,avg_frame_rate:format=duration,bit_rate",
        "-of", "json", path,
    ])
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe не смог прочитать файл:\n{result.stderr[-400:]}")
    parsed = json.loads(result.stdout)
    streams = parsed.get("streams") or []
    if not streams:
        raise RuntimeError("в файле нет видеодорожки")
    s = streams[0]
    fmt = parsed.get("format") or {}
    num, _, den = (s.get("avg_frame_rate") or "0/1").partition("/")
    fps = float(num) / float(den or 1) if float(den or 1) else 0.0
    return {
        "codec": s.get("codec_name", ""),
        "width": int(s.get("width") or 0),
        "height": int(s.get("height") or 0),
        "pix_fmt": s.get("pix_fmt", ""),
        "hdr": s.get("color_transfer", "") in HDR_TRANSFERS,
        "fps": fps,
        "duration": float(fmt.get("duration") or 0),
        "bitrate": int(fmt.get("bit_rate") or 0),
    }


def describe(info: dict | None) -> str:
    """Short human-readable summary, e.g. '1080×1920 · 60 fps · H.264 · 15.4 Мбит/с · 0:15'."""
    if not info:
        return ""
    codec = {"h264": "H.264", "hevc": "HEVC", "vp9": "VP9", "av1": "AV1"}.get(info["codec"], info["codec"].upper())
    parts = [f"{info['width']}×{info['height']}", f"{info['fps']:.0f} fps", codec]
    if info["hdr"]:
        parts.append("HDR")
    if info["bitrate"]:
        parts.append(f"{info['bitrate'] / 1e6:.1f} Мбит/с")
    if info["duration"]:
        total = round(info["duration"])
        parts.append(f"{total // 60}:{total % 60:02d}")
    return " · ".join(parts)


def target_short_side(info: dict, keep_4k: bool) -> int | None:
    """Short side to downscale to, or None to keep the resolution.

    By default everything is brought to 1080p. With keep_4k, resolution is kept
    unless a side exceeds TikTok's 4096 px upload limit, then it goes to 2160p.
    """
    short, long = sorted((info["width"], info["height"]))
    if not keep_4k:
        return 1080 if short > 1080 else None
    return 2160 if long > 4096 else None


def choose_mode(path: str, info: dict | None, force_encode: bool,
                keep_4k: bool = False) -> tuple[str, str]:
    """Return (mode, reason)."""
    if force_encode:
        return "encode", "включена принудительная перекодировка"
    is_mp4 = Path(path).suffix.lower() == ".mp4"
    if info is None:
        if is_mp4:
            return "patch", "ffmpeg не найден, патчу как есть"
        raise RuntimeError("для этого формата нужен ffmpeg, а он не найден")
    if info["hdr"]:
        return "encode", "HDR-видео, перевожу в SDR"
    if info["codec"] not in PASSTHROUGH_CODECS:
        return "encode", f"кодек {info['codec']} не подходит"
    if "10" in info["pix_fmt"] or "12" in info["pix_fmt"]:
        return "encode", f"{info['pix_fmt']} — перевожу в 8 бит"
    target = target_short_side(info, keep_4k)
    if target:
        return "encode", f"{info['width']}x{info['height']} — уменьшаю до {target}p"
    if info["fps"] > 61:
        return "encode", f"{info['fps']:.0f} fps — снижаю до 60"
    if not is_mp4:
        return "remux", "не MP4, перепаковываю без потери качества"
    return "patch", "исходник подходит, качество не трогаю"


def _encode(ffmpeg: str, src: str, dst: str, info: dict | None, keep_4k: bool):
    filters = []
    if info and info["hdr"]:
        filters.append(
            "zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,"
            "tonemap=tonemap=hable:desat=0,zscale=t=bt709:m=bt709:r=tv"
        )
    target = target_short_side(info, keep_4k) if info else None
    if target:
        if info["width"] <= info["height"]:
            filters.append(f"scale={target}:-2:flags=lanczos")
        else:
            filters.append(f"scale=-2:{target}:flags=lanczos")
    out_short = target or (min(info["width"], info["height"]) if info else 1080)
    if info and info["fps"] > 61:
        filters.append("fps=60")
    filters.append("format=yuv420p")
    # ffmpeg 9 ignores -color_trc etc. for libx264, so tag colors via the filter
    filters.append("setparams=color_primaries=bt709:color_trc=bt709:colorspace=bt709")
    cmd = [
        ffmpeg, "-y", "-i", src,
        "-map", "0:v:0", "-map", "0:a:0?",
        "-vf", ",".join(filters),
        # "slow" on 4K takes ages, "medium" keeps encode times sane at the same CRF
        "-c:v", "libx264", "-preset", "slow" if out_short <= 1080 else "medium",
        "-crf", "16", "-profile:v", "high",
        "-c:a", "aac", "-b:a", "256k",
        "-movflags", "+faststart", dst,
    ]
    result = _run(cmd)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg: ошибка перекодировки\n{result.stderr[-600:]}")


def _remux(ffmpeg: str, src: str, dst: str, info: dict):
    cmd = [
        ffmpeg, "-y", "-i", src,
        "-map", "0:v:0", "-map", "0:a:0?",
        "-c", "copy", "-movflags", "+faststart",
    ]
    if info["codec"] == "hevc":
        cmd += ["-tag:v", "hvc1"]
    cmd.append(dst)
    result = _run(cmd)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg: ошибка перепаковки\n{result.stderr[-600:]}")


def output_path(src: str) -> str:
    p = Path(src)
    return str(p.with_name(f"{p.stem}_tiktok.mp4"))


def process(src: str, force_encode: bool = False, log=print, on_plan=None,
            keep_4k: bool = False) -> tuple[str, str]:
    """Prepare src for upload. Returns (output path, mode).

    on_plan(info, mode, reason) is called once the mode is chosen, before the slow part.
    """
    info = probe(src)
    if info:
        log(describe(info))
    mode, reason = choose_mode(src, info, force_encode, keep_4k)
    log(f"режим: {MODE_LABELS[mode]} ({reason})")
    if on_plan:
        on_plan(info, mode, reason)

    dst = output_path(src)
    tmp = dst + ".part"
    try:
        if mode == "patch":
            shutil.copyfile(src, tmp)
        else:
            ffmpeg = find_tool("ffmpeg")
            if not ffmpeg:
                raise RuntimeError("ffmpeg не найден")
            log("перекодирую, это может занять пару минут..." if mode == "encode"
                else "перепаковываю...")
            # ffmpeg picks the muxer by extension, so give it a real .mp4 name
            tmp_mp4 = tmp + ".mp4"
            if mode == "encode":
                _encode(ffmpeg, src, tmp_mp4, info, keep_4k)
            else:
                _remux(ffmpeg, src, tmp_mp4, info)
            os.replace(tmp_mp4, tmp)
        patcher.patch_mp4_mvhd(tmp)
        os.replace(tmp, dst)
    finally:
        for leftover in (tmp, tmp + ".mp4"):
            if os.path.exists(leftover):
                os.remove(leftover)
    log(f"готово: {Path(dst).name}")
    return dst, mode


def preview(path: str):
    ffplay = find_tool("ffplay")
    if not ffplay:
        raise RuntimeError("ffplay не найден (ставится вместе с ffmpeg)")
    subprocess.Popen(
        [ffplay, "-loglevel", "error", "-window_title", Path(path).name,
         "-y", "800", "-loop", "0", path],
        creationflags=NO_WINDOW,
    )


if __name__ == "__main__":
    force = "--encode" in sys.argv
    keep = "--4k" in sys.argv
    for arg in sys.argv[1:]:
        if not arg.startswith("--"):
            process(arg, force_encode=force, keep_4k=keep)
