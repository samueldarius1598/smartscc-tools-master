"""Reusable vertically scrollable frame for long Tk layouts."""

from __future__ import annotations

import weakref
import tkinter as tk
from tkinter import ttk


class ScrollableFrame(tk.Frame):
    """Canvas-backed container with a vertical scrollbar and safe mouse wheel handling."""

    WHEEL_DELTA_STEP = 120
    SCROLL_UNITS_PER_STEP = 1
    _active_frame_ref: weakref.ReferenceType["ScrollableFrame"] | None = None
    _bound_roots: "weakref.WeakKeyDictionary[tk.Misc, bool]" = weakref.WeakKeyDictionary()
    _ignored_widget_classes = {
        "Text",
        "Canvas",
        "Listbox",
        "Treeview",
        "Scrollbar",
        "TScrollbar",
    }

    def __init__(self, parent: tk.Widget, bg: str = "#FFFFFF") -> None:
        super().__init__(parent, bg=bg)
        self._bg = bg
        self._mousewheel_remainder = 0
        self._scrollregion_after_id: str | None = None
        self._contains_cache: dict[int, bool] = {}
        self._ignore_cache: dict[int, bool] = {}

        self.canvas = tk.Canvas(
            self,
            bg=bg,
            highlightthickness=0,
            borderwidth=0,
        )
        self.scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.scrollbar.set)

        self.canvas.pack(side="left", fill="both", expand=True)
        self.scrollbar.pack(side="right", fill="y")

        self.interior = tk.Frame(self.canvas, bg=bg)
        self._window_id = self.canvas.create_window((0, 0), window=self.interior, anchor="nw")

        self.interior.bind("<Configure>", self._on_interior_configure, add="+")
        self.canvas.bind("<Configure>", self._on_canvas_configure, add="+")
        for widget in (self, self.canvas, self.interior):
            widget.bind("<Enter>", self._on_pointer_enter, add="+")
            widget.bind("<Leave>", self._on_pointer_leave, add="+")

        self._ensure_root_bindings()

    def _ensure_root_bindings(self) -> None:
        root = self.winfo_toplevel()
        if root in self._bound_roots:
            return
        root.bind_all("<MouseWheel>", ScrollableFrame._dispatch_mousewheel, add="+")
        root.bind_all("<Button-4>", ScrollableFrame._dispatch_mousewheel, add="+")
        root.bind_all("<Button-5>", ScrollableFrame._dispatch_mousewheel, add="+")
        self._bound_roots[root] = True

    @classmethod
    def _dispatch_mousewheel(cls, event: tk.Event) -> str | None:
        active = cls._active_frame_ref() if cls._active_frame_ref is not None else None
        if active is None or not active.winfo_exists():
            return None
        return active._on_mousewheel(event)

    def _on_interior_configure(self, _event: tk.Event) -> None:
        if self._scrollregion_after_id is not None:
            try:
                self.after_cancel(self._scrollregion_after_id)
            except (ValueError, tk.TclError):
                pass
        self._scrollregion_after_id = self.after_idle(self._update_scrollregion)

    def _update_scrollregion(self) -> None:
        self._scrollregion_after_id = None
        if self.winfo_exists():
            self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_canvas_configure(self, event: tk.Event) -> None:
        self.canvas.itemconfigure(self._window_id, width=event.width)

    def _on_pointer_enter(self, _event: tk.Event) -> None:
        ScrollableFrame._active_frame_ref = weakref.ref(self)
        self._contains_cache.clear()
        self._ignore_cache.clear()

    def _on_pointer_leave(self, _event: tk.Event) -> None:
        self.after_idle(self._clear_active_if_pointer_left)

    def _clear_active_if_pointer_left(self) -> None:
        if not self.winfo_exists():
            return
        pointer_widget = self.winfo_containing(self.winfo_pointerx(), self.winfo_pointery())
        if pointer_widget is None or not self._contains_widget(pointer_widget):
            active = ScrollableFrame._active_frame_ref() if ScrollableFrame._active_frame_ref is not None else None
            if active is self:
                ScrollableFrame._active_frame_ref = None

    def _on_mousewheel(self, event: tk.Event) -> str | None:
        if not self.winfo_exists():
            return None
        event_widget = event.widget if self._is_widget_like(getattr(event, "widget", None)) else None
        if event_widget is None or not self._contains_widget(event_widget):
            return None
        if self._should_ignore_widget(event_widget):
            return None

        delta = self._raw_mousewheel_delta(event)
        step_delta = self._consume_mousewheel_steps(delta)
        if step_delta == 0:
            return "break"

        first, last = self.canvas.yview()
        if step_delta < 0 and first <= 0.0:
            self._mousewheel_remainder = 0
            return None
        if step_delta > 0 and last >= 1.0:
            self._mousewheel_remainder = 0
            return None

        self.canvas.yview_scroll(step_delta * self.SCROLL_UNITS_PER_STEP, "units")
        return "break"

    def _should_ignore_widget(self, widget: tk.Misc) -> bool:
        wid = id(widget)
        cached = self._ignore_cache.get(wid)
        if cached is not None:
            return cached
        result = self._walk_should_ignore(widget)
        self._ignore_cache[wid] = result
        return result

    def _walk_should_ignore(self, widget: tk.Misc) -> bool:
        current: tk.Misc | None = widget
        while current is not None:
            if current in {self, self.canvas, self.interior}:
                return False
            if current.winfo_class() in self._ignored_widget_classes:
                return True
            current = getattr(current, "master", None)
        return False

    def _contains_widget(self, widget: tk.Misc) -> bool:
        wid = id(widget)
        cached = self._contains_cache.get(wid)
        if cached is not None:
            return cached
        result = self._walk_contains(widget)
        self._contains_cache[wid] = result
        return result

    def _walk_contains(self, widget: tk.Misc) -> bool:
        current: tk.Misc | None = widget
        while current is not None:
            if current is self:
                return True
            current = getattr(current, "master", None)
        return False

    @staticmethod
    def _is_widget_like(widget: object | None) -> bool:
        return widget is not None and hasattr(widget, "winfo_class")

    @classmethod
    def _steps_from_total_delta(cls, total_delta: int) -> int:
        if total_delta > 0:
            return -(total_delta // cls.WHEEL_DELTA_STEP)
        if total_delta < 0:
            return (-total_delta) // cls.WHEEL_DELTA_STEP
        return 0

    @classmethod
    def _remainder_after_steps(cls, total_delta: int, step_delta: int) -> int:
        if step_delta == 0:
            return total_delta
        return total_delta + (step_delta * cls.WHEEL_DELTA_STEP)

    def _consume_mousewheel_steps(self, raw_delta: int) -> int:
        if raw_delta == 0:
            return 0
        total_delta = self._mousewheel_remainder + raw_delta
        step_delta = self._steps_from_total_delta(total_delta)
        self._mousewheel_remainder = self._remainder_after_steps(total_delta, step_delta)
        return step_delta

    @classmethod
    def _raw_mousewheel_delta(cls, event: tk.Event) -> int:
        num = getattr(event, "num", None)
        if num == 4:
            return cls.WHEEL_DELTA_STEP
        if num == 5:
            return -cls.WHEEL_DELTA_STEP

        delta = int(getattr(event, "delta", 0) or 0)
        return delta
