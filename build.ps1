# Local build: creates .venv if needed and produces dist\TikTok-HD-Uploader.exe
Set-Location $PSScriptRoot

function Invoke-Step([string]$exe, [string[]]$arguments) {
    # PyInstaller logs to stderr, so judge success by exit code, not by output stream
    & $exe @arguments 2>&1 | ForEach-Object { "$_" }
    if ($LASTEXITCODE -ne 0) { throw "$exe $($arguments -join ' ') failed with exit code $LASTEXITCODE" }
}

if (-not (Test-Path ".venv")) {
    Invoke-Step "python" @("-m", "venv", ".venv")
}
$python = ".\.venv\Scripts\python.exe"
Invoke-Step $python @("-m", "pip", "install", "--disable-pip-version-check", "-q", "-r", "requirements-build.txt")
Invoke-Step $python @("-m", "PyInstaller", "--noconfirm", "--clean", "tiktok-hd-uploader.spec")

$exe = "dist\TikTok-HD-Uploader.exe"
$hash = (Get-FileHash $exe -Algorithm SHA256).Hash.ToLower()
Write-Host "`nBuilt $exe"
Write-Host "SHA256 $hash"
