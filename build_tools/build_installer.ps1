[CmdletBinding()]
param(
    [string]$Version = "1.0.0",
    [switch]$SkipTests,
    [string]$BuildRoot = "",
    [string]$GitHubRepo = "",
    [string]$ManifestFileName = "latest.json"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Get-PythonExecutable {
    param(
        [string]$RepoRoot
    )

    $candidates = @(
        (Join-Path $RepoRoot ".venv\Scripts\python.exe"),
        "python"
    )

    foreach ($candidate in $candidates) {
        try {
            & $candidate --version *> $null
            if ($LASTEXITCODE -eq 0) {
                return $candidate
            }
        } catch {
            continue
        }
    }

    throw "Python interpreter tidak ditemukan. Pastikan .venv aktif atau python tersedia di PATH."
}

function Get-IExpressExecutable {
    $command = Get-Command iexpress.exe -ErrorAction SilentlyContinue
    if ($command -and $command.Source) {
        return $command.Source
    }

    $candidates = @(
        "C:\Windows\System32\iexpress.exe",
        "C:\WINDOWS\system32\iexpress.exe"
    )

    foreach ($candidate in $candidates) {
        if (Test-Path $candidate) {
            return $candidate
        }
    }

    throw "IExpress tidak ditemukan. Tool ini biasanya sudah tersedia di Windows."
}

function Get-InnoSetupCompiler {
    $command = Get-Command ISCC.exe -ErrorAction SilentlyContinue
    if ($command -and $command.Source) {
        return $command.Source
    }

    $candidates = @(
        "C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
        "C:\Program Files\Inno Setup 6\ISCC.exe"
    )

    foreach ($candidate in $candidates) {
        if (Test-Path $candidate) {
            return $candidate
        }
    }

    return ""
}

function Get-BuildRootPath {
    param(
        [string]$RequestedBuildRoot
    )

    if (-not [string]::IsNullOrWhiteSpace($RequestedBuildRoot)) {
        return $RequestedBuildRoot
    }

    if (-not [string]::IsNullOrWhiteSpace($env:TEMP)) {
        return (Join-Path $env:TEMP "itu_smartscc_build")
    }

    throw "BuildRoot tidak diberikan dan env:TEMP tidak tersedia."
}

function Get-GitHubRepoSlug {
    param(
        [string]$RequestedRepo
    )

    if (-not [string]::IsNullOrWhiteSpace($RequestedRepo)) {
        return $RequestedRepo.Trim()
    }

    if (-not [string]::IsNullOrWhiteSpace($env:SMARTSCC_GITHUB_REPO)) {
        return $env:SMARTSCC_GITHUB_REPO.Trim()
    }

    return "YOUR-ORG/YOUR-REPO"
}

function Invoke-Step {
    param(
        [string]$Title,
        [scriptblock]$Action
    )

    Write-Host "==> $Title"
    & $Action
}

function Invoke-PythonCommand {
    param(
        [string]$PythonExecutable,
        [string[]]$Arguments,
        [string]$ExtraPythonPath = ""
    )

    $previousPythonPath = $env:PYTHONPATH
    try {
        if (-not [string]::IsNullOrWhiteSpace($ExtraPythonPath)) {
            if ([string]::IsNullOrWhiteSpace($previousPythonPath)) {
                $env:PYTHONPATH = $ExtraPythonPath
            } else {
                $env:PYTHONPATH = "$ExtraPythonPath;$previousPythonPath"
            }
        }

        & $PythonExecutable @Arguments | Out-Host
        return $LASTEXITCODE
    } finally {
        if ($null -eq $previousPythonPath) {
            Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
        } else {
            $env:PYTHONPATH = $previousPythonPath
        }
    }
}

function Ensure-PyInstaller {
    param(
        [string]$PythonExecutable,
        [string]$TargetDir
    )

    $pyInstallerMain = Join-Path $TargetDir "PyInstaller\__main__.py"
    if (Test-Path $pyInstallerMain) {
        return
    }

    if (Test-Path $TargetDir) {
        Remove-Item -Recurse -Force $TargetDir
    }
    New-Item -ItemType Directory -Force -Path $TargetDir | Out-Null

    & $PythonExecutable -m pip install --upgrade --target $TargetDir pyinstaller
    if ($LASTEXITCODE -ne 0) {
        throw "Gagal install PyInstaller ke folder build sementara."
    }
}

$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $repoRoot
$pythonExe = Get-PythonExecutable -RepoRoot $repoRoot

$stagingRoot = [System.IO.Path]::GetFullPath((Get-BuildRootPath -RequestedBuildRoot $BuildRoot))
$releaseRoot = Join-Path $repoRoot "build\release"
$pyInstallerSiteDir = Join-Path $stagingRoot "pyinstaller-site"
$pyInstallerWorkDir = Join-Path $stagingRoot "pyinstaller-work"
$pyInstallerDistDir = Join-Path $stagingRoot "app"
$installerTempOutputDir = Join-Path $stagingRoot "installer"
$installerPayloadDir = Join-Path $stagingRoot "installer-payload"
$installerOutputDir = Join-Path $releaseRoot "installer"
$installerTempPath = Join-Path $installerTempOutputDir ("SmartsCC-ToolsMaster-Setup-{0}.exe" -f $Version)
$installerSedPath = Join-Path $installerTempOutputDir "installer.sed"
$innoScriptPath = Join-Path $PSScriptRoot "packaging\windows\installer.iss"
$specFile = Join-Path $PSScriptRoot "packaging\windows\tools_master_gui.spec"
$installScriptSource = Join-Path $PSScriptRoot "packaging\windows\install_app.ps1"
$appOutputDir = Join-Path $pyInstallerDistDir "smartscc_tools_master"

# Every recursive cleanup must remain inside the selected build staging root.
$stagingPrefix = $stagingRoot.TrimEnd('\') + '\'
foreach ($cleanupPath in @($pyInstallerSiteDir, $appOutputDir, $installerPayloadDir)) {
    $resolvedCleanupPath = [System.IO.Path]::GetFullPath($cleanupPath)
    if (-not $resolvedCleanupPath.StartsWith($stagingPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Build cleanup path escapes staging root: $resolvedCleanupPath"
    }
    $cursor = $resolvedCleanupPath
    while ($cursor) {
        if ((Test-Path -LiteralPath $cursor) -and (Get-Item -LiteralPath $cursor -Force).LinkType) {
            throw "Build cleanup path contains a link: $cursor"
        }
        $cursor = Split-Path -Parent $cursor
    }
}

New-Item -ItemType Directory -Force -Path $releaseRoot | Out-Null
New-Item -ItemType Directory -Force -Path $stagingRoot | Out-Null
New-Item -ItemType Directory -Force -Path $pyInstallerSiteDir | Out-Null
New-Item -ItemType Directory -Force -Path $pyInstallerWorkDir | Out-Null
New-Item -ItemType Directory -Force -Path $pyInstallerDistDir | Out-Null
New-Item -ItemType Directory -Force -Path $installerTempOutputDir | Out-Null
New-Item -ItemType Directory -Force -Path $installerPayloadDir | Out-Null
New-Item -ItemType Directory -Force -Path $installerOutputDir | Out-Null

if (Test-Path $appOutputDir) {
    Remove-Item -Recurse -Force $appOutputDir
}
if (Test-Path $installerTempPath) {
    Remove-Item -Force $installerTempPath
}
if (Test-Path $installerSedPath) {
    Remove-Item -Force $installerSedPath
}
if (Test-Path $installerPayloadDir) {
    Get-ChildItem -Path $installerPayloadDir -Force | Remove-Item -Recurse -Force
}

Invoke-Step -Title "Ensure PyInstaller tersedia" -Action {
    Ensure-PyInstaller -PythonExecutable $pythonExe -TargetDir $pyInstallerSiteDir
}

if (-not $SkipTests) {
    Invoke-Step -Title "Jalankan unit test" -Action {
        & $pythonExe -m unittest discover -s tests -v
        if ($LASTEXITCODE -ne 0) {
            throw "Unit test gagal. Build dihentikan."
        }
    }
}

Invoke-Step -Title "Build aplikasi GUI dengan PyInstaller" -Action {
    $exitCode = Invoke-PythonCommand `
        -PythonExecutable $pythonExe `
        -ExtraPythonPath $pyInstallerSiteDir `
        -Arguments @(
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--clean",
            "--distpath",
            $pyInstallerDistDir,
            "--workpath",
            $pyInstallerWorkDir,
            $specFile
        )

    if ($exitCode -ne 0) {
        throw "PyInstaller build gagal."
    }
}

if (-not (Test-Path (Join-Path $appOutputDir "smartscc_tools_master.exe"))) {
    throw "Executable hasil build tidak ditemukan di $appOutputDir."
}

Invoke-Step -Title "Siapkan payload installer" -Action {
    $payloadZipPath = Join-Path $installerPayloadDir "app_payload.zip"
    Copy-Item -Path $installScriptSource -Destination (Join-Path $installerPayloadDir "install_app.ps1") -Force

    if (Test-Path $payloadZipPath) {
        Remove-Item -Force $payloadZipPath
    }

    Compress-Archive -Path (Join-Path $appOutputDir "*") -DestinationPath $payloadZipPath -Force
}

Invoke-Step -Title "Build Setup.exe" -Action {
    $innoCompiler = Get-InnoSetupCompiler
    if (-not [string]::IsNullOrWhiteSpace($innoCompiler)) {
        & $innoCompiler /Qp "/DAppVersion=$Version" "/DSourceDir=$appOutputDir" "/DOutputDir=$installerTempOutputDir" $innoScriptPath
        if ($LASTEXITCODE -ne 0) {
            throw "Inno Setup build gagal dengan exit code $LASTEXITCODE."
        }
        return
    }

    $iexpress = Get-IExpressExecutable
    $sedContent = @"
[Version]
Class=IEXPRESS
SEDVersion=3
[Options]
PackagePurpose=InstallApp
ShowInstallProgramWindow=1
HideExtractAnimation=1
UseLongFileName=1
InsideCompressed=0
CAB_FixedSize=0
CAB_ResvCodeSigning=0
RebootMode=N
InstallPrompt=
DisplayLicense=
FinishMessage=Install selesai. Shortcut desktop sudah dibuat.
TargetName=$installerTempPath
FriendlyName=Smart's CC Tools Master Setup
AppLaunched=powershell.exe -ExecutionPolicy Bypass -File install_app.ps1
PostInstallCmd=<None>
AdminQuietInstCmd=
UserQuietInstCmd=
SourceFiles=SourceFiles
[SourceFiles]
SourceFiles0=$installerPayloadDir\
[SourceFiles0]
install_app.ps1=
app_payload.zip=
"@

    $sedContent = $sedContent.Replace("`r`n", "`n").Replace("`n", "`r`n")
    Set-Content -LiteralPath $installerSedPath -Value $sedContent -Encoding ASCII
    $process = Start-Process -FilePath $iexpress -WorkingDirectory $installerTempOutputDir -ArgumentList "/N", (Split-Path -Leaf $installerSedPath) -WindowStyle Hidden -PassThru
    $processHandle = $process.Handle
    $process.WaitForExit()
    if ($process.ExitCode -ne 0) {
        throw "IExpress build gagal dengan exit code $($process.ExitCode)."
    }
}

if (-not (Test-Path $installerTempPath)) {
    throw "Installer tidak ditemukan di $installerTempPath."
}

$finalInstallerPath = Join-Path $installerOutputDir (Split-Path -Leaf $installerTempPath)
Copy-Item -Path $installerTempPath -Destination $finalInstallerPath -Force

Invoke-Step -Title "Generate latest.json manifest" -Action {
    $repoSlug = Get-GitHubRepoSlug -RequestedRepo $GitHubRepo
    $installerFileName = Split-Path -Leaf $finalInstallerPath
    $installerHash = (Get-FileHash -Path $finalInstallerPath -Algorithm SHA256).Hash.ToLowerInvariant()
    $releaseBaseUrl = "https://github.com/$repoSlug/releases"
    $manifestPath = Join-Path $installerOutputDir $ManifestFileName

    $manifestPayload = [ordered]@{
        version = $Version
        installer_url = "$releaseBaseUrl/download/v$Version/$installerFileName"
        sha256 = $installerHash
        release_notes_url = "$releaseBaseUrl/tag/v$Version"
        published_at = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
    }

    $manifestPayload | ConvertTo-Json | Set-Content -Path $manifestPath -Encoding ASCII
}

Write-Host ""
Write-Host "Installer selesai dibuat:"
Write-Host $finalInstallerPath
Write-Host (Join-Path $installerOutputDir $ManifestFileName)
