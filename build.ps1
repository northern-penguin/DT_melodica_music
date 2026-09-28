param([string]$Conda = "D:\ProgramData\anaconda3\Scripts\conda.exe")

$ErrorActionPreference = "Stop"
$project = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $project
if (-not (Test-Path -LiteralPath $Conda)) { throw "找不到 Conda：$Conda" }

# 所有构建命令都由 DTmusic 环境执行；应用以单目录方式交付。
$prefix = Join-Path (Split-Path -Parent (Split-Path -Parent $Conda)) "envs\DTmusic"
if (-not (Test-Path -LiteralPath $prefix)) { throw "DTmusic 环境不存在" }
& $Conda run -n DTmusic python -m PyInstaller --noconfirm --clean --onedir --windowed --name DTmusic --add-data "songs.json;." --collect-all pypdfium2 --collect-all pypdfium2_raw dtmusic_gui.py
if ($LASTEXITCODE -ne 0) { throw "PyInstaller 构建失败" }

$package = Join-Path $project "dist\DTmusic"
$tools = Join-Path $package "_internal\tools\tesseract"
New-Item -ItemType Directory -Path $tools -Force | Out-Null
$bin = Join-Path $prefix "Library\bin"
$ocrExe = Join-Path $bin "tesseract.exe"
if (-not (Test-Path -LiteralPath $ocrExe)) { throw "DTmusic 环境缺少 Tesseract" }
Copy-Item -LiteralPath $ocrExe -Destination $tools -Force
# Conda 的 Tesseract 在 Windows 上使用动态库；复制环境的 Library\bin
# 可覆盖语言识别和图像解码所需依赖，代价是包体积增加。
Get-ChildItem -LiteralPath $bin -Filter "*.dll" -File | Copy-Item -Destination $tools -Force
$data = Join-Path $prefix "Library\share\tessdata"
if (-not (Test-Path -LiteralPath $data)) { throw "DTmusic 环境缺少 tessdata" }
$model = Join-Path $data "eng.traineddata"
if (-not (Test-Path -LiteralPath $model)) {
    # Conda 的 Tesseract 可能不带语言模型；下载官方快速英语模型。
    Invoke-WebRequest -Uri "https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/main/eng.traineddata" -OutFile $model
}
Copy-Item -LiteralPath $data -Destination $tools -Recurse -Force

# 官方 Windows 发行版自带 Java 运行时；使用 MSI 的管理安装方式提取，
# 不修改系统安装状态。将原版程序与 DTmusic 分目录保存。
$audVersion = "5.11.0"
$vendorDir = Join-Path $project "vendor"
New-Item -ItemType Directory -Path $vendorDir -Force | Out-Null
$audMsi = Join-Path $vendorDir "Audiveris-$audVersion-windows-x86_64.msi"
$audSource = Join-Path $vendorDir "Audiveris-$audVersion-source.zip"
if (-not (Test-Path -LiteralPath $audMsi)) {
    Invoke-WebRequest -Uri "https://github.com/Audiveris/audiveris/releases/download/$audVersion/Audiveris-$audVersion-windows-x86_64.msi" -OutFile $audMsi
}
if ((Get-FileHash -LiteralPath $audMsi -Algorithm SHA256).Hash -ne "AC221B0D39A90E32B7F43DBF9F5D5A45FF8B5424076AF21B65278955688DAC71") {
    throw "Audiveris MSI 校验失败"
}
if (-not (Test-Path -LiteralPath $audSource)) {
    Invoke-WebRequest -Uri "https://github.com/Audiveris/audiveris/archive/refs/tags/$audVersion.zip" -OutFile $audSource
}
if ((Get-FileHash -LiteralPath $audSource -Algorithm SHA256).Hash -ne "5F63CD1BDC7F48A9AFADFA616AB9F2D4C923B7D7DBFF46712E2B3EF65E896616") {
    throw "Audiveris 源码校验失败"
}
$audExtract = Join-Path $vendorDir "Audiveris-$audVersion-extracted"
$audProgram = Join-Path $audExtract "Audiveris"
if (-not (Test-Path -LiteralPath (Join-Path $audProgram "Audiveris.exe"))) {
    $msiArgs = "/a `"$audMsi`" /qn TARGETDIR=`"$audExtract`""
    $msiProcess = Start-Process -FilePath "msiexec.exe" -ArgumentList $msiArgs -Wait -PassThru -WindowStyle Hidden
    if ($msiProcess.ExitCode -ne 0) { throw "Audiveris MSI 提取失败：$($msiProcess.ExitCode)" }
}
$audTarget = Join-Path $package "_internal\tools\audiveris"
Copy-Item -LiteralPath $audProgram -Destination $audTarget -Recurse -Force

$licenseDir = Join-Path $package "licenses"
New-Item -ItemType Directory -Path $licenseDir -Force | Out-Null
# 与二进制一并保留实际安装包提供的授权文本。
$packages = Join-Path (Split-Path -Parent (Split-Path -Parent $prefix)) "pkgs"
Get-ChildItem -LiteralPath (Join-Path $prefix "conda-meta") -Filter "*.json" -File | ForEach-Object {
    $source = Join-Path $packages "$($_.BaseName)\info\licenses"
    if (Test-Path -LiteralPath $source) {
        Copy-Item -LiteralPath $source -Destination (Join-Path $licenseDir $_.BaseName) -Recurse -Force
    }
}
$pdfLicenses = Get-ChildItem -LiteralPath (Join-Path $prefix "Lib\site-packages") -Directory -Filter "pypdfium2-*.dist-info" | Select-Object -First 1
if ($pdfLicenses) {
    Copy-Item -LiteralPath (Join-Path $pdfLicenses.FullName "licenses") -Destination (Join-Path $licenseDir "pypdfium2-pdfium") -Recurse -Force
}
Invoke-WebRequest -Uri "https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/main/LICENSE" -OutFile (Join-Path $licenseDir "tessdata_fast-LICENSE.txt")
Copy-Item -LiteralPath $audSource -Destination (Join-Path $licenseDir "Audiveris-$audVersion-source.zip") -Force
@"
Audiveris $audVersion is bundled as an unmodified, separately launched program.
Upstream release: https://github.com/Audiveris/audiveris/releases/tag/$audVersion
Corresponding source: Audiveris-$audVersion-source.zip in this directory.
Audiveris license: GNU AGPL version 3; see LICENSE inside the source archive.
The bundled Java runtime's notices are retained under tools/audiveris/runtime/legal.
"@ | Set-Content -LiteralPath (Join-Path $licenseDir "Audiveris-NOTICE.txt") -Encoding UTF8
Copy-Item -LiteralPath (Join-Path $project "README.md") -Destination $package -Force
Copy-Item -LiteralPath (Join-Path $project "environment.yml") -Destination $package -Force

$zip = Join-Path $project "dist\DTmusic-Windows.zip"
if (Test-Path -LiteralPath $zip) { Remove-Item -LiteralPath $zip -Force }
Compress-Archive -LiteralPath $package -DestinationPath $zip -CompressionLevel Optimal
Write-Host "构建完成：$zip"
