"""Shared branding asset paths and window-branding helpers."""

from __future__ import annotations

import ctypes
from dataclasses import dataclass
from pathlib import Path
import sys
import tkinter as tk


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RESOURCE_ROOT = PROJECT_ROOT if getattr(sys, "frozen", False) else PROJECT_ROOT / "build_tools"
ICON_DIR = RESOURCE_ROOT / "icon"
ASSETS_DIR = RESOURCE_ROOT / "assets"

MAIN_LOGO_PNG = ICON_DIR / "mainlogo.png"
MAIN_LOGO_ICO = ICON_DIR / "mainlogo.ico"
EDIT_STOCK_MOVEMENT_ICON = ICON_DIR / "iconEditStockMovement.png"
ITEM_JOURNAL_ICON = ICON_DIR / "iconInternalTransfer.png"
FIXING_UNLINK_SVL_ICON = ICON_DIR / "iconFixingSVLItemJournal.png"
UPDATE_STANDARD_COST_ICON = ICON_DIR / "iconUpdateStandardPrice.png"
APP_USER_MODEL_ID = "smartscc.tools.master"
WM_SETICON = 0x0080
ICON_SMALL = 0
ICON_BIG = 1
IMAGE_ICON = 1
LR_LOADFROMFILE = 0x0010
LR_DEFAULTSIZE = 0x0040


@dataclass(frozen=True)
class WindowBrandingResult:
    app_user_model_id_applied: bool
    photo_icon_applied: bool
    bitmap_icon_applied: bool
    native_icon_applied: bool


def set_windows_app_user_model_id(app_id: str = APP_USER_MODEL_ID) -> bool:
    """Set the Windows taskbar identity for the current process."""

    if sys.platform != "win32":
        return False
    try:
        shell32 = ctypes.windll.shell32
        result = shell32.SetCurrentProcessExplicitAppUserModelID(str(app_id))
    except Exception:
        return False
    return int(result) >= 0


def is_native_window_branding_ready(result: WindowBrandingResult | None) -> bool:
    """Return whether native Win32 icon branding was applied."""

    return bool(result and result.native_icon_applied)


def apply_tk_window_icon_assets(window: tk.Misc) -> tuple[bool, bool]:
    """Apply Tk-managed icon assets for the window."""

    photo_applied = False
    bitmap_applied = False

    if MAIN_LOGO_PNG.exists():
        try:
            icon_image = tk.PhotoImage(file=str(MAIN_LOGO_PNG))
            window.iconphoto(True, icon_image)
            setattr(window, "_smartscc_app_icon", icon_image)
            photo_applied = True
        except tk.TclError:
            photo_applied = False

    if MAIN_LOGO_ICO.exists():
        try:
            window.iconbitmap(default=str(MAIN_LOGO_ICO))
            bitmap_applied = True
        except tk.TclError:
            bitmap_applied = False

    return photo_applied, bitmap_applied


def apply_windows_native_window_icon(window: tk.Misc, icon_path: Path = MAIN_LOGO_ICO) -> bool:
    """Push the app icon into the native Win32 window for Alt+Tab/titlebar usage."""

    if sys.platform != "win32" or not icon_path.exists():
        return False

    try:
        if hasattr(window, "update_idletasks"):
            window.update_idletasks()
        hwnd = int(window.winfo_id())
        user32 = ctypes.windll.user32
        big_icon = user32.LoadImageW(
            None,
            str(icon_path),
            IMAGE_ICON,
            32,
            32,
            LR_LOADFROMFILE | LR_DEFAULTSIZE,
        )
        small_icon = user32.LoadImageW(
            None,
            str(icon_path),
            IMAGE_ICON,
            16,
            16,
            LR_LOADFROMFILE | LR_DEFAULTSIZE,
        )
        if not big_icon and not small_icon:
            return False
        if big_icon:
            user32.SendMessageW(hwnd, WM_SETICON, ICON_BIG, big_icon)
        if small_icon:
            user32.SendMessageW(hwnd, WM_SETICON, ICON_SMALL, small_icon)
    except Exception:
        return False
    return True


def apply_window_branding(window: tk.Misc) -> WindowBrandingResult:
    """Apply the shared app icon and Windows taskbar identity to a Tk window."""

    app_user_model_id_applied = set_windows_app_user_model_id()
    photo_icon_applied, bitmap_icon_applied = apply_tk_window_icon_assets(window)
    native_icon_applied = apply_windows_native_window_icon(window)
    return WindowBrandingResult(
        app_user_model_id_applied=app_user_model_id_applied,
        photo_icon_applied=photo_icon_applied,
        bitmap_icon_applied=bitmap_icon_applied,
        native_icon_applied=native_icon_applied,
    )
