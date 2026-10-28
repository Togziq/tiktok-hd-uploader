# PyInstaller build spec. Build with: pyinstaller --noconfirm --clean tiktok-hd-uploader.spec
from PyInstaller.utils.hooks import collect_all

datas = [("assets/icon.ico", "assets")]
binaries = []
hiddenimports = []
for package in ("customtkinter", "tkinterdnd2"):
    pkg_datas, pkg_binaries, pkg_hidden = collect_all(package)
    datas += pkg_datas
    binaries += pkg_binaries
    hiddenimports += pkg_hidden

a = Analysis(
    ["app.py"],
    pathex=["."],
    datas=datas,
    binaries=binaries,
    hiddenimports=hiddenimports,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="TikTok-HD-Uploader",
    icon="assets/icon.ico",
    console=False,
    upx=False,
)
