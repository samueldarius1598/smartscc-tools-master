"""Sidebar navigation widget with module buttons and settings."""

from __future__ import annotations

import tkinter as tk
from pathlib import Path
from typing import Callable

from smartscc_tools.core import theme as T
from smartscc_tools.core.module_base import ModuleBase
from smartscc_tools.widgets.image_utils import load_icon


class SidebarWidget(tk.Frame):
    """Dark sidebar with module navigation buttons."""

    SETTINGS_ID = "__settings__"
    _TEXT_WRAP_WIDTH = max(96, T.SIDEBAR_WIDTH - 4 - 24 - 34 - 10)

    def __init__(
        self,
        parent: tk.Widget,
        modules: list[ModuleBase],
        on_select: Callable[[str], None],
    ) -> None:
        super().__init__(parent, bg=T.BG_SIDEBAR, width=T.SIDEBAR_WIDTH)
        self.pack_propagate(False)
        self._on_select = on_select
        self._items: dict[str, tk.Frame] = {}
        self._active_id: str = ""
        self._icon_refs: list[tk.PhotoImage] = []

        # Module buttons
        for module in modules:
            self._add_item(module.module_id, module.display_name, module.icon_path)

        # Separator
        sep = tk.Frame(self, bg="#4A4A60", height=1)
        sep.pack(fill="x", padx=16, pady=8)

        # Settings button
        self._add_item(self.SETTINGS_ID, "Settings", None, icon_text="\u2699")

    def _add_item(
        self, item_id: str, label: str,
        icon_path: str | None, icon_text: str = "",
    ) -> None:
        item_frame = tk.Frame(self, bg=T.BG_SIDEBAR, cursor="hand2")
        item_frame.pack(fill="x")

        # Active indicator (left accent bar)
        accent = tk.Frame(item_frame, bg=T.BG_SIDEBAR, width=4)
        accent.pack(side="left", fill="y")

        content = tk.Frame(item_frame, bg=T.BG_SIDEBAR, padx=12, pady=10)
        content.pack(side="left", fill="x", expand=True)

        # Icon
        icon_label = None
        if icon_path and Path(icon_path).exists():
            try:
                img = load_icon(icon_path, target_height=24, master=self)
                if img is not None:
                    self._icon_refs.append(img)
                    icon_label = tk.Label(content, image=img, bg=T.BG_SIDEBAR)
                    icon_label.pack(side="left", padx=(0, 10))
            except (tk.TclError, RuntimeError):
                pass

        if icon_label is None and icon_text:
            tk.Label(content, text=icon_text, bg=T.BG_SIDEBAR, fg=T.TEXT_SIDEBAR,
                     font=T.font(14)).pack(side="left", padx=(0, 10))

        text_label = tk.Label(
            content,
            text=label,
            bg=T.BG_SIDEBAR,
            fg=T.TEXT_SIDEBAR,
            font=T.font(T.FONT_BODY_SIZE),
            anchor="nw",
            justify="left",
            wraplength=self._TEXT_WRAP_WIDTH,
        )
        text_label.pack(side="left", fill="both", expand=True)

        # Store references for highlighting
        self._items[item_id] = item_frame
        item_frame._accent = accent  # type: ignore[attr-defined]
        item_frame._text_label = text_label  # type: ignore[attr-defined]

        # Click handlers on all sub-widgets
        for widget in (item_frame, content, text_label):
            widget.bind("<Button-1>", lambda e, iid=item_id: self._on_click(iid))
            widget.bind("<Enter>", lambda e, iid=item_id: self._on_hover(iid, True))
            widget.bind("<Leave>", lambda e, iid=item_id: self._on_hover(iid, False))

    def _on_click(self, item_id: str) -> None:
        self.set_active(item_id)
        self._on_select(item_id)

    def _on_hover(self, item_id: str, entering: bool) -> None:
        if item_id == self._active_id:
            return
        frame = self._items.get(item_id)
        if frame is None:
            return
        bg = T.BG_SIDEBAR_HOVER if entering else T.BG_SIDEBAR
        if getattr(frame, "_current_bg", None) == bg:
            return
        frame._current_bg = bg  # type: ignore[attr-defined]
        self._set_item_bg(frame, bg)

    def set_active(self, item_id: str) -> None:
        # Deactivate previous
        if self._active_id and self._active_id in self._items:
            prev = self._items[self._active_id]
            self._set_item_bg(prev, T.BG_SIDEBAR)
            prev._current_bg = T.BG_SIDEBAR  # type: ignore[attr-defined]
            prev._accent.configure(bg=T.BG_SIDEBAR)  # type: ignore[attr-defined]
            prev._text_label.configure(fg=T.TEXT_SIDEBAR)  # type: ignore[attr-defined]

        # Activate new
        self._active_id = item_id
        if item_id in self._items:
            curr = self._items[item_id]
            self._set_item_bg(curr, T.BG_SIDEBAR_ACTIVE)
            curr._current_bg = T.BG_SIDEBAR_ACTIVE  # type: ignore[attr-defined]
            curr._accent.configure(bg=T.BRAND_SECONDARY)  # type: ignore[attr-defined]
            curr._text_label.configure(fg=T.TEXT_SIDEBAR_ACTIVE)  # type: ignore[attr-defined]

    @staticmethod
    def _set_item_bg(frame: tk.Frame, bg: str) -> None:
        frame.configure(bg=bg)
        for child in frame.winfo_children():
            try:
                child.configure(bg=bg)
            except tk.TclError:
                pass
            for grandchild in child.winfo_children():
                try:
                    grandchild.configure(bg=bg)
                except tk.TclError:
                    pass
