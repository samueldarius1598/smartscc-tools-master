Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$appName = "Smart's CC Tools Master"
$appExeName = "smartscc_tools_master.exe"
$payloadName = "app_payload.zip"
$appUserModelId = "smartscc.tools.master"
$requiredRuntimeFiles = @(
    "_internal\python313.dll",
    "_internal\base_library.zip",
    "_internal\VCRUNTIME140.dll",
    "_internal\VCRUNTIME140_1.dll"
)

function Expand-PayloadZip {
    param(
        [string]$ZipPath,
        [string]$DestinationPath
    )

    Add-Type -AssemblyName System.IO.Compression.FileSystem

    $destinationFullPath = [System.IO.Path]::GetFullPath($DestinationPath)
    $destinationPrefix = $destinationFullPath.TrimEnd('\') + '\'
    $archive = [System.IO.Compression.ZipFile]::OpenRead($ZipPath)
    try {
        foreach ($entry in $archive.Entries) {
            $targetPath = [System.IO.Path]::GetFullPath((Join-Path $DestinationPath $entry.FullName))
            if (-not $targetPath.StartsWith($destinationPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
                throw "Payload installer mengandung path tidak valid: $($entry.FullName)"
            }

            if ([string]::IsNullOrEmpty($entry.Name)) {
                New-Item -ItemType Directory -Force -Path $targetPath | Out-Null
                continue
            }

            $targetDir = Split-Path -Parent $targetPath
            if (-not [string]::IsNullOrWhiteSpace($targetDir)) {
                New-Item -ItemType Directory -Force -Path $targetDir | Out-Null
            }

            [System.IO.Compression.ZipFileExtensions]::ExtractToFile($entry, $targetPath, $true)
        }
    } finally {
        $archive.Dispose()
    }
}

function Assert-InstalledRuntime {
    param(
        [string]$InstallPath
    )

    foreach ($relativePath in $requiredRuntimeFiles) {
        $absolutePath = Join-Path $InstallPath $relativePath
        if (-not (Test-Path -LiteralPath $absolutePath)) {
            throw "File runtime wajib tidak ditemukan setelah extract: $absolutePath"
        }
    }
}

function Ensure-ShortcutAppUserModelIdSupport {
    if ("SmartsCC.ShortcutPropertyStore" -as [type]) {
        return
    }

    Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;

namespace SmartsCC {
    [ComImport]
    [Guid("00021401-0000-0000-C000-000000000046")]
    internal class ShellLink {
    }

    [ComImport]
    [InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    [Guid("0000010b-0000-0000-C000-000000000046")]
    internal interface IPersistFile {
        void GetClassID(out Guid pClassID);
        void IsDirty();
        void Load([MarshalAs(UnmanagedType.LPWStr)] string pszFileName, uint dwMode);
        void Save([MarshalAs(UnmanagedType.LPWStr)] string pszFileName, bool fRemember);
        void SaveCompleted([MarshalAs(UnmanagedType.LPWStr)] string pszFileName);
        void GetCurFile([MarshalAs(UnmanagedType.LPWStr)] out string ppszFileName);
    }

    [StructLayout(LayoutKind.Sequential, Pack = 4)]
    internal struct PROPERTYKEY {
        public Guid fmtid;
        public uint pid;

        public PROPERTYKEY(Guid formatId, uint propertyId) {
            fmtid = formatId;
            pid = propertyId;
        }
    }

    [StructLayout(LayoutKind.Explicit)]
    internal struct PROPVARIANT {
        [FieldOffset(0)]
        public ushort vt;

        [FieldOffset(8)]
        public IntPtr pointerValue;

        public static PROPVARIANT FromString(string value) {
            var propVariant = new PROPVARIANT();
            propVariant.vt = 31;
            propVariant.pointerValue = Marshal.StringToCoTaskMemUni(value);
            return propVariant;
        }

        public void Clear() {
            PropVariantClear(ref this);
        }

        [DllImport("ole32.dll")]
        private static extern int PropVariantClear(ref PROPVARIANT pvar);
    }

    [ComImport]
    [InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    [Guid("886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99")]
    internal interface IPropertyStore {
        void GetCount(out uint cProps);
        void GetAt(uint iProp, out PROPERTYKEY pkey);
        void GetValue(ref PROPERTYKEY key, out PROPVARIANT pv);
        void SetValue(ref PROPERTYKEY key, ref PROPVARIANT propvar);
        void Commit();
    }

    public static class ShortcutPropertyStore {
        private const uint STGM_READWRITE = 0x00000002;
        private static readonly PROPERTYKEY AppUserModelIdKey =
            new PROPERTYKEY(new Guid("9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3"), 5);

        public static void SetShortcutAppUserModelId(string shortcutPath, string appUserModelId) {
            object shellLink = new ShellLink();
            var persistFile = (IPersistFile)shellLink;
            persistFile.Load(shortcutPath, STGM_READWRITE);
            var propertyStore = (IPropertyStore)shellLink;
            var propValue = PROPVARIANT.FromString(appUserModelId);

            try {
                propertyStore.SetValue(ref AppUserModelIdKey, ref propValue);
                propertyStore.Commit();
                persistFile.Save(shortcutPath, true);
            } finally {
                propValue.Clear();
                if (Marshal.IsComObject(shellLink)) {
                    Marshal.FinalReleaseComObject(shellLink);
                }
            }
        }
    }
}
"@
}

function Set-ShortcutAppUserModelId {
    param(
        [string]$ShortcutPath,
        [string]$AppUserModelId
    )

    if ([string]::IsNullOrWhiteSpace($ShortcutPath) -or [string]::IsNullOrWhiteSpace($AppUserModelId)) {
        return $false
    }
    if (-not (Test-Path -LiteralPath $ShortcutPath)) {
        return $false
    }

    try {
        Ensure-ShortcutAppUserModelIdSupport
        [SmartsCC.ShortcutPropertyStore]::SetShortcutAppUserModelId($ShortcutPath, $AppUserModelId)
        return $true
    } catch {
        return $false
    }
}

function New-Shortcut {
    param(
        [string]$ShortcutPath,
        [string]$TargetPath,
        [string]$WorkingDirectory,
        [string]$IconPath,
        [string]$AppUserModelId
    )

    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut($ShortcutPath)
    $shortcut.TargetPath = $TargetPath
    $shortcut.WorkingDirectory = $WorkingDirectory
    $shortcut.IconLocation = $(if ([string]::IsNullOrWhiteSpace($IconPath)) { $TargetPath } else { $IconPath })
    $shortcut.Save()
    [void](Set-ShortcutAppUserModelId -ShortcutPath $ShortcutPath -AppUserModelId $AppUserModelId)
}

Add-Type -AssemblyName System.Windows.Forms

try {
    $payloadRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
    $zipPath = Join-Path $payloadRoot $payloadName
    if (-not (Test-Path $zipPath)) {
        throw "Payload installer tidak ditemukan: $zipPath"
    }

    $programsRoot = Join-Path $env:LOCALAPPDATA "Programs"
    $installDir = Join-Path $programsRoot $appName
    $stagingDir = Join-Path $programsRoot ($appName + ".__staging")

    if (Test-Path $stagingDir) {
        Remove-Item -Recurse -Force $stagingDir
    }
    New-Item -ItemType Directory -Force -Path $stagingDir | Out-Null
    Expand-PayloadZip -ZipPath $zipPath -DestinationPath $stagingDir
    Assert-InstalledRuntime -InstallPath $stagingDir

    if (Test-Path $installDir) {
        Remove-Item -Recurse -Force $installDir
    }
    Move-Item -Path $stagingDir -Destination $installDir

    $appExePath = Join-Path $installDir $appExeName
    if (-not (Test-Path $appExePath)) {
        throw "Executable hasil install tidak ditemukan: $appExePath"
    }
    $appIconPath = Join-Path $installDir "_internal\\icon\\mainlogo.ico"
    if (-not (Test-Path $appIconPath)) {
        $appIconPath = $appExePath
    }

    $desktopShortcut = Join-Path ([Environment]::GetFolderPath("Desktop")) "$appName.lnk"
    $startMenuShortcut = Join-Path ([Environment]::GetFolderPath("Programs")) "$appName.lnk"

    New-Shortcut -ShortcutPath $desktopShortcut -TargetPath $appExePath -WorkingDirectory $installDir -IconPath $appIconPath -AppUserModelId $appUserModelId
    New-Shortcut -ShortcutPath $startMenuShortcut -TargetPath $appExePath -WorkingDirectory $installDir -IconPath $appIconPath -AppUserModelId $appUserModelId

    [System.Windows.Forms.MessageBox]::Show(
        "Install selesai.`r`nShortcut desktop dan Start Menu sudah dibuat.",
        $appName,
        [System.Windows.Forms.MessageBoxButtons]::OK,
        [System.Windows.Forms.MessageBoxIcon]::Information
    ) | Out-Null
} catch {
    [System.Windows.Forms.MessageBox]::Show(
        $_.Exception.Message,
        "$appName Installer",
        [System.Windows.Forms.MessageBoxButtons]::OK,
        [System.Windows.Forms.MessageBoxIcon]::Error
    ) | Out-Null
    exit 1
}
