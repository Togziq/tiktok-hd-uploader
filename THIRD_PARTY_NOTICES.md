# Third-party notices

## tiktok-lossless-upload

`tiktok_lossless_patch.py` is copied unmodified from
[buwryme/tiktok-lossless-upload](https://github.com/buwryme/tiktok-lossless-upload)
at commit `f2a4ed9536578b689d2c642943a287bb1da4fc65`.

License: MIT, Copyright (c) 2026 miles. Full text: [licenses/tiktok-lossless-upload.txt](licenses/tiktok-lossless-upload.txt).

This app only calls its `patch_mp4_mvhd` function, which works offline.
The rest of that module (including the remote config download) is not used.

## Libraries bundled into the .exe

| Library | License |
|---|---|
| [CustomTkinter](https://github.com/TomSchimansky/CustomTkinter) | MIT |
| [tkinterdnd2](https://github.com/Eliav2/tkinterdnd2) | MIT |
| [Pillow](https://github.com/python-pillow/Pillow) | MIT-CMU (HPND) |
| [Python](https://www.python.org/) / Tcl/Tk | PSF License / Tcl/Tk License |

## FFmpeg

FFmpeg is **not** bundled. The app uses an FFmpeg installation on the user's machine
(for example `winget install Gyan.FFmpeg`). FFmpeg is licensed under LGPL/GPL, see
[ffmpeg.org/legal.html](https://ffmpeg.org/legal.html).
