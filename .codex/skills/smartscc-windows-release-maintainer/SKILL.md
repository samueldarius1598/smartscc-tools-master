---
name: smartscc-windows-release-maintainer
description: Maintain the Windows build, installer, and branding flow for Smart's CC Tools Master. Use when Codex needs to build the installer, refresh shipped branding, change the main logo, fix title bar or taskbar or Alt+Tab branding, update packaging files, troubleshoot the installed Windows app, or verify release artifacts for this repo.
---

# Smartscc Windows Release Maintainer

## Overview

Use this skill after loading repo defaults when the work touches Smart's CC Tools Master Windows packaging or branding. Read [references/release-map.md](references/release-map.md) first for the exact file map and command path.

## Default Workflow

1. Read the actual release path before editing.
Start with `build_installer.ps1`, `smartscc_tools/branding.py`, `uild_tools/packaging/windows/install_app.ps1`, `uild_tools/packaging/windows/installer.iss`, and `uild_tools/packaging/windows/tools_master_gui.spec`.

2. Keep stable branding names intact.
Treat `uild_tools/icon/mainlogo.png` and `uild_tools/icon/mainlogo.ico` as the shipped branding targets. If a new artwork file such as `uild_tools/icon/mainLogoV2.png` appears, derive or copy it into `mainlogo.png` and regenerate `mainlogo.ico` instead of retargeting code to the temporary filename.

3. Keep runtime branding synchronized.
Preserve the process-level `set_windows_app_user_model_id()` bootstrap before the root window is created. Only the dashboard shell owns `tk.Tk()`, and the main window branding should still be reapplied once after the root is mapped.

4. Keep packaging layers synchronized.
If the shipped logo or icon changes, make sure `branding.py`, `tools_master_gui.spec`, `installer.iss`, and `install_app.ps1` still point to the same `mainlogo.ico`.

5. Refresh the shipped app, not only tests.
For changes in branding, icons, packaging, installer logic, or bundled assets, run the release build path:
`powershell -ExecutionPolicy Bypass -File .\build_tools\build_installer.ps1 -Version 1.0.0`

When the installer metadata is meant for real distribution, pass a real `-GitHubRepo owner/repo` value to `build_installer.ps1`; otherwise `latest.json` will keep placeholder release URLs.

6. Verify the installed path when the user reports Windows behavior.
Compare the latest build output with `%LOCALAPPDATA%\Programs\Smart's CC Tools Master` before assuming the newest code is running there.

## Repo Rules

- Keep `uild_tools/icon/` as the branding source of truth.
- Keep `mainlogo.ico` multi-size and preserve at least `16x16` and `32x32`.
- Use targeted branding and packaging tests for fast feedback, then run the full `unittest discover` path through the release build when the shipped app changed.
- Treat `.lnk` `AppUserModelID` stamping as best-effort unless readback proves otherwise.
- Expect Windows icon cache to affect taskbar, Alt+Tab, and title bar validation.

## Useful Triggers

- "build installer terbaru"
- "ganti main logo lalu build ulang"
- "branding Windows tidak muncul"
- "fix icon Alt+Tab / title bar / taskbar"
- "installer sudah di-install tapi app yang jalan masih lama"

## Avoid

- Do not add another `tk.Tk()` root for branding tests or fixes.
- Do not rename the stable `mainlogo.*` targets unless the repo is intentionally migrating every reference together.
- Do not stop at targeted tests when the user needs a fresh installer or refreshed local install.
