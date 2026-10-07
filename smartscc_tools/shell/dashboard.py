"""Main dashboard window — root Tk with header, sidebar, and module area."""

from __future__ import annotations

import logging
import tkinter as tk
from typing import Any

from smartscc_tools import APP_NAME, APP_VERSION
from smartscc_tools.branding import apply_window_branding, is_native_window_branding_ready
from smartscc_tools.core import theme as T
from smartscc_tools.core.auth import AuthInfo, AutoAuthService
from smartscc_tools.core.global_config import GlobalPersistentState
from smartscc_tools.core.module_base import ModuleContext
from smartscc_tools.core.module_registry import ModuleRegistry
from smartscc_tools.services.odoo.master_cache import SessionMasterCache
from smartscc_tools.shell.header import HeaderWidget
from smartscc_tools.shell.module_frame import ModuleFrameContainer
from smartscc_tools.shell.sidebar import SidebarWidget


def _sanitize_geometry(geometry: str) -> str:
    """Strip off-screen positions (e.g. +-32000 from minimized state) from a geometry string."""
    import re
    # geometry format: WxH or WxH+X+Y or WxH-X+Y etc. X/Y can be +-32000 (two signs).
    m = re.match(r"(\d+x\d+)(.*)", geometry.strip())
    if not m:
        return "1280x820"
    size = m.group(1)
    pos_str = m.group(2)
    if not pos_str:
        return size
    # Extract all integers (ignoring sign characters) from position part
    nums = re.findall(r"-?\d+", pos_str)
    if len(nums) >= 2:
        x = int(nums[0])
        y = int(nums[1])
        if x < -500 or y < -500:
            return size  # off-screen: drop position, let OS place the window
    return geometry.strip()


def _load_global_settings_panel_class():
    from smartscc_tools.shell.settings_panel import GlobalSettingsPanel

    return GlobalSettingsPanel


class DashboardWindow:
    """Root window that hosts sidebar navigation and module panels."""

    def __init__(self) -> None:
        self._logger = logging.getLogger("smartscc_tools")
        if not self._logger.handlers:
            handler = logging.StreamHandler()
            handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(message)s", "%H:%M:%S"))
            self._logger.addHandler(handler)
            self._logger.setLevel(logging.DEBUG)
        self._technical_logger = logging.getLogger("smartscc_tools.technical")

        # --- Tk root ---
        self.root = tk.Tk()
        self.root.title(APP_NAME)
        self.root.geometry("1280x820")
        self.root.minsize(1040, 760)
        self.root.configure(bg=T.BG_MAIN)
        self._window_branding_result = apply_window_branding(self.root)
        self._window_branding_refresh_binding_id: str | None = None
        self._window_branding_refresh_scheduled = False

        # --- Global state ---
        self._state_store = GlobalPersistentState()
        self._settings = self._state_store.load()
        if self._settings.window_geometry.strip():
            _safe_geometry = _sanitize_geometry(self._settings.window_geometry)
            self.root.geometry(_safe_geometry)

        # --- Auth (auto-enter stub) ---
        self._auth_service = AutoAuthService(logger=self._logger)
        self._auth_info = self._auth_service.authenticate()
        self._settings_panel: tk.Frame | None = None

        # --- Module registry ---
        self._registry = ModuleRegistry.create_default()
        self._master_cache = SessionMasterCache(ttl_seconds=600)

        # --- Module context ---
        self._context = ModuleContext(
            global_settings=self._settings,
            auth_info=self._auth_info,
            logger=self._logger,
            technical_logger=self._technical_logger,
            root=self.root,
            status_callback=self._update_status,
            master_cache=self._master_cache,
        )

        # --- Build UI ---
        self._build_ui()
        self._schedule_post_map_branding_refresh()

        # --- Restore last active module ---
        active_id = self._settings.active_module_id
        modules = self._registry.all_modules()
        if not any(m.module_id == active_id for m in modules):
            active_id = modules[0].module_id if modules else ""
        if active_id:
            self._sidebar.set_active(active_id)
            self._module_container.show(active_id)

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._auth_service.start_background_hydration(on_update=self._schedule_auth_refresh)

    def _build_ui(self) -> None:
        # --- Header ---
        self._header = HeaderWidget(self.root, auth_info=self._auth_info)
        self._header.pack(fill="x")

        # --- Middle area (sidebar + content) ---
        middle = tk.Frame(self.root, bg=T.BG_MAIN)
        middle.pack(fill="both", expand=True)

        # Sidebar
        modules = self._registry.all_modules()
        self._sidebar = SidebarWidget(middle, modules=modules, on_select=self._on_module_selected)
        self._sidebar.pack(side="left", fill="y")

        # Module content area
        self._module_container = ModuleFrameContainer(middle, context=self._context)
        self._module_container.pack(side="left", fill="both", expand=True)

        # Register modules in container
        for module in modules:
            self._module_container.register_module(module)

        # --- Footer ---
        footer = tk.Frame(self.root, bg=T.BG_FOOTER, height=T.FOOTER_HEIGHT)
        footer.pack(fill="x", side="bottom")
        footer.pack_propagate(False)

        tk.Label(footer, text=T.FOOTER_TEXT, bg=T.BG_FOOTER, fg=T.TEXT_ON_DARK,
                 font=T.font(T.FONT_FOOTER_SIZE, bold=True)).pack(side="left", padx=16)

        self._status_var = tk.StringVar(value="Ready")
        tk.Label(footer, textvariable=self._status_var, bg=T.BG_FOOTER,
                 fg="#888888", font=T.font(T.FONT_FOOTER_SIZE)).pack(side="left", expand=True)

        tk.Label(footer, text=f"v{APP_VERSION}", bg=T.BG_FOOTER, fg="#666666",
                 font=T.font(T.FONT_FOOTER_SIZE)).pack(side="right", padx=16)

    def _schedule_post_map_branding_refresh(self) -> None:
        self._window_branding_refresh_binding_id = self.root.bind("<Map>", self._on_root_map_for_branding, add="+")

    def _on_root_map_for_branding(self, event: object | None = None) -> None:
        if getattr(event, "widget", self.root) is not self.root:
            return
        if self._window_branding_refresh_scheduled:
            return
        self._window_branding_refresh_scheduled = True
        if self._window_branding_refresh_binding_id:
            self.root.unbind("<Map>", self._window_branding_refresh_binding_id)
            self._window_branding_refresh_binding_id = None
        self.root.after_idle(self._refresh_window_branding_after_map)

    def _refresh_window_branding_after_map(self) -> None:
        self._window_branding_result = apply_window_branding(self.root)
        if not is_native_window_branding_ready(self._window_branding_result):
            self._logger.debug("Native window icon branding did not apply after root map.")

    def _ensure_settings_panel(self) -> tk.Frame:
        if self._settings_panel is None:
            panel_cls = _load_global_settings_panel_class()
            self._settings_panel = panel_cls(
                self._module_container,
                self._settings,
                self._state_store,
                root=self.root,
                status_callback=self._update_status,
                shutdown_callback=self._shutdown_for_update,
            )
        return self._settings_panel

    def _on_module_selected(self, module_id: str) -> None:
        if module_id == SidebarWidget.SETTINGS_ID:
            self._module_container.show_settings(self._ensure_settings_panel())
        else:
            self._module_container.show(module_id)
            self._settings.active_module_id = module_id
            self._state_store.save(self._settings)

    def _update_status(self, message: str) -> None:
        if hasattr(self, "_status_var"):
            self._status_var.set(message)

    def show_module(self, module_id: str) -> None:
        self._sidebar.set_active(module_id)
        self._on_module_selected(module_id)

    def get_module(self, module_id: str) -> object | None:
        return self._module_container.get_module(module_id)

    def build_gui_debug_snapshot(self, *, module_id: str | None = None, **kwargs: Any) -> dict[str, Any]:
        active_module_id = self._module_container.active_module_id
        target_module_id = module_id or active_module_id
        snapshot: dict[str, Any] = {
            "app_name": APP_NAME,
            "active_module_id": active_module_id,
            "target_module_id": target_module_id,
            "window_title": str(self.root.title() or ""),
            "window_geometry": str(self.root.geometry() or ""),
        }
        if not target_module_id:
            return snapshot
        module = self._module_container.get_module(target_module_id)
        if module is None:
            snapshot["module_state"] = None
            return snapshot
        build_snapshot = getattr(module, "build_gui_debug_snapshot", None)
        snapshot["module_state"] = build_snapshot(**kwargs) if callable(build_snapshot) else None
        return snapshot

    def _schedule_auth_refresh(self, auth_info: AuthInfo) -> None:
        self.root.after(0, lambda: self._apply_auth_update(auth_info))

    def _apply_auth_update(self, auth_info: AuthInfo) -> None:
        self._auth_info = auth_info
        self._context.auth_info = auth_info
        self._header.update_auth_info(auth_info)

    def _on_close(self) -> None:
        self._shutdown_for_update()

    def _shutdown_for_update(self) -> None:
        self._settings.window_geometry = self.root.geometry()
        self._module_container.shutdown_all()
        self._state_store.save(self._settings)
        self.root.destroy()

    def close(self) -> None:
        self._shutdown_for_update()

    def run(self) -> int:
        self.root.mainloop()
        return 0
