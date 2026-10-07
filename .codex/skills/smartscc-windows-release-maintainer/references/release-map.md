# Smart's CC Tools Master Windows Release Map

## Key Files

- `build_installer.ps1`: release build entrypoint, unit-test gate, PyInstaller build, installer output, `latest.json` generation.
- `smartscc_tools/branding.py`: asset constants, `AppUserModelID`, Tk icon hooks, native Win32 icon application.
- `smartscc_tools/entrypoints/gui.py`: process branding bootstrap before the dashboard window is created.
- `smartscc_tools/shell/dashboard.py`: one-shot post-map branding refresh for the root window.
- `uild_tools/packaging/windows/tools_master_gui.spec`: packaged executable icon path.
- `uild_tools/packaging/windows/installer.iss`: installer icon metadata, uninstall icon, desktop and Start Menu icon metadata.
- `uild_tools/packaging/windows/install_app.ps1`: payload extraction, runtime DLL validation, shortcut creation, best-effort shortcut `AppUserModelID` stamping.

## Branding Asset Workflow

- Treat `uild_tools/icon/mainlogo.png` as the runtime image source.
- Treat `uild_tools/icon/mainlogo.ico` as the Windows packaging source.
- If a new artwork file arrives, update `mainlogo.png` and regenerate `mainlogo.ico` from it.
- Preserve small ICO sizes at minimum: `16x16` and `32x32`.

## Fast Verification

- Run `python -m unittest tests.test_branding_assets tests.test_window_branding tests.test_dashboard_branding tests.test_packaging_update -v` for targeted branding and packaging checks.
- Run `python -m unittest discover -s tests -v` or the release build script when the shipped app changed.

## Release Build

- Command:
  `powershell -ExecutionPolicy Bypass -File .\build_tools\build_installer.ps1 -Version 1.0.0`
- Output installer:
  `build\release\installer\SmartsCC-ToolsMaster-Setup-1.0.0.exe`
- Output manifest:
  `build\release\installer\latest.json`
- Local installed app path:
  `%LOCALAPPDATA%\Programs\Smart's CC Tools Master`

## Known Pitfalls

- Development icons/templates live under `build_tools/`; packaged runtime assets
  remain under `_internal/icon` and `_internal/assets`. Resolve the application
  import root separately from the `build_tools/packaging/windows` spec location.
- Invoke IExpress from its SED directory with the plain SED basename; a quoted
  absolute SED argument can be rejected. Write ASCII/CRLF and retain the process
  handle before waiting for its exit code. Details:
  `C:/Users/User/.ai-shared/memories/windows-iexpress-packaging.md`.
- Missing runtime DLLs in `_internal` will break the installed app even if tests pass.
- A stale local install can make a correct new build look broken.
- Windows icon cache can keep showing old taskbar or Alt+Tab branding after a correct rebuild.
- Shortcut `System.AppUserModel.ID` writeback may not read back reliably even when the best-effort setter runs.
