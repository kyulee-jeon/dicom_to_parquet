# Build dicom_to_parquet_gui.py into a standalone Windows .exe
#
#   .\dcm_venv\Scripts\activate
#   powershell -ExecutionPolicy Bypass -File .\build_exe.ps1
#
# Result: .\dist\DICOM_Metadata_Extractor.exe (one file, no Python required)
#
# Note on the DLLs below: this project's interpreter is an Anaconda build, and
# CPython extension modules there link against DLLs that live in
# <base_prefix>\Library\bin, which PyInstaller does not scan by default.
# Without libexpat.dll the frozen app dies at startup with
# "DLL load failed while importing pyexpat".

param(
    [string]$Name = "DICOM_Metadata_Extractor",
    [switch]$Console,          # build with a console window (useful for debugging)
    [string]$DistPath = ".\dist",
    [string]$WorkPath = ".\build"
)

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

$libBin = & python -c "import sys,os; print(os.path.join(sys.base_prefix,'Library','bin'))"
if (Test-Path $libBin) {
    $env:PATH = "$libBin;$env:PATH"
    Write-Host "Using DLL search path: $libBin"
}

$args = @(
    "--noconfirm", "--onefile", "--clean",
    "--name", $Name,
    "--distpath", $DistPath,
    "--workpath", $WorkPath,
    "--specpath", $WorkPath,
    "--collect-submodules", "pyarrow",
    "--exclude-module", "matplotlib",
    "--exclude-module", "pandas",
    "--exclude-module", "scipy",
    "--exclude-module", "IPython",
    "--exclude-module", "notebook",
    "--exclude-module", "numba",
    "--exclude-module", "tkinter"
)

if ($Console) { $args += "--console" } else { $args += "--windowed" }

foreach ($dll in @("libexpat.dll", "libssl-3-x64.dll", "libcrypto-3-x64.dll")) {
    $p = Join-Path $libBin $dll
    if (Test-Path $p) { $args += @("--add-binary", "$p;.") }
}

$args += "dicom_to_parquet_gui.py"

Write-Host "python -m PyInstaller $($args -join ' ')"
& python -m PyInstaller @args
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed with exit code $LASTEXITCODE" }

Write-Host ""
Write-Host ("Built: " + (Resolve-Path (Join-Path $DistPath "$Name.exe")))
