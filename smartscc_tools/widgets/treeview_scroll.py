"""Shared mousewheel helpers for ttk.Treeview widgets."""

from __future__ import annotations

from types import SimpleNamespace
import tkinter as tk
from tkinter import ttk


_WHEEL_DELTA_STEP = 120


def _raw_mousewheel_delta(event: tk.Event) -> int:
    num = getattr(event, "num", None)
    if num == 4:
        return _WHEEL_DELTA_STEP
    if num == 5:
        return -_WHEEL_DELTA_STEP
    return int(getattr(event, "delta", 0) or 0)


def _consume_scroll_steps(widget: ttk.Treeview, raw_delta: int, *, axis: str) -> int:
    if raw_delta == 0:
        return 0
    remainder_attr = "_treeview_wheel_remainder_x" if axis == "x" else "_treeview_wheel_remainder_y"
    remainder = int(getattr(widget, remainder_attr, 0) or 0)
    total_delta = remainder + raw_delta
    if total_delta > 0:
        step_delta = -(total_delta // _WHEEL_DELTA_STEP)
    else:
        step_delta = (-total_delta) // _WHEEL_DELTA_STEP
    if step_delta == 0:
        setattr(widget, remainder_attr, total_delta)
        return 0
    setattr(widget, remainder_attr, total_delta + (step_delta * _WHEEL_DELTA_STEP))
    return step_delta


def bind_treeview_scroll_support(tree: ttk.Treeview) -> ttk.Treeview:
    """Attach vertical and horizontal wheel scrolling to a Treeview."""

    setattr(tree, "_treeview_wheel_remainder_x", 0)
    setattr(tree, "_treeview_wheel_remainder_y", 0)

    def _scroll_y(event: tk.Event) -> str | None:
        steps = _consume_scroll_steps(tree, _raw_mousewheel_delta(event), axis="y")
        if steps == 0:
            return "break"
        tree.yview_scroll(steps, "units")
        return "break"

    def _scroll_x(event: tk.Event) -> str | None:
        steps = _consume_scroll_steps(tree, _raw_mousewheel_delta(event), axis="x")
        if steps == 0:
            return "break"
        tree.xview_scroll(steps, "units")
        return "break"

    def _scroll_y_linux(event: tk.Event) -> str | None:
        linux_event = SimpleNamespace(num=getattr(event, "num", None), delta=0)
        return _scroll_y(linux_event)  # type: ignore[arg-type]

    def _scroll_x_linux(event: tk.Event) -> str | None:
        linux_event = SimpleNamespace(num=getattr(event, "num", None), delta=0)
        return _scroll_x(linux_event)  # type: ignore[arg-type]

    tree.bind("<MouseWheel>", _scroll_y, add="+")
    tree.bind("<Shift-MouseWheel>", _scroll_x, add="+")
    tree.bind("<Button-4>", _scroll_y_linux, add="+")
    tree.bind("<Button-5>", _scroll_y_linux, add="+")
    tree.bind("<Shift-Button-4>", _scroll_x_linux, add="+")
    tree.bind("<Shift-Button-5>", _scroll_x_linux, add="+")
    return tree


__all__ = ["bind_treeview_scroll_support"]
