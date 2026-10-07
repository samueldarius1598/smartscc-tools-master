"""Container that manages showing/hiding module frames."""

from __future__ import annotations

import tkinter as tk

from smartscc_tools.core import theme as T
from smartscc_tools.core.module_base import ModuleBase, ModuleContext


class ModuleFrameContainer(tk.Frame):
    """Manages lazy creation and switching of module UI frames."""

    def __init__(self, parent: tk.Widget, context: ModuleContext) -> None:
        super().__init__(parent, bg=T.BG_MAIN)
        self._context = context
        self._frames: dict[str, tk.Frame] = {}
        self._modules: dict[str, ModuleBase] = {}
        self._active_id: str = ""

    def register_module(self, module: ModuleBase) -> None:
        self._modules[module.module_id] = module

    @property
    def active_module_id(self) -> str:
        if self._active_id in self._modules:
            return self._active_id
        return ""

    def get_module(self, module_id: str) -> ModuleBase | None:
        module = self._modules.get(module_id)
        loaded_module = getattr(module, "_loaded_module", None)
        if loaded_module is not None:
            return loaded_module
        return module

    def _hide_active_frame(self) -> None:
        if self._active_id and self._active_id in self._modules:
            self._modules[self._active_id].on_deactivate()
        if self._active_id == "__settings__" and self._active_id in self._frames:
            on_hide = getattr(self._frames[self._active_id], "on_hide", None)
            if callable(on_hide):
                on_hide()
        if self._active_id and self._active_id in self._frames:
            self._frames[self._active_id].pack_forget()

    def show(self, module_id: str) -> None:
        self._hide_active_frame()

        # Lazy-create if first visit
        if module_id not in self._frames and module_id in self._modules:
            frame = tk.Frame(self, bg=T.BG_MAIN)
            module = self._modules[module_id]
            module.create_ui(frame, self._context)
            self._frames[module_id] = frame

        # Show
        if module_id in self._frames:
            self._frames[module_id].pack(fill="both", expand=True)
            self._active_id = module_id
            self._modules[module_id].on_activate()

    def show_settings(self, settings_frame: tk.Frame) -> None:
        """Show the global settings panel."""
        self._hide_active_frame()

        # Settings frame is managed externally
        if "__settings__" in self._frames:
            self._frames["__settings__"].pack_forget()
        self._frames["__settings__"] = settings_frame
        settings_frame.pack(fill="both", expand=True)
        on_show = getattr(settings_frame, "on_show", None)
        if callable(on_show):
            on_show()
        self._active_id = "__settings__"

    def shutdown_all(self) -> None:
        for module in self._modules.values():
            module.on_shutdown()
