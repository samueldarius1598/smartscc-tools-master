"""Helpers for bounded text log widgets."""

from __future__ import annotations

import tkinter as tk
from typing import Sequence


DEFAULT_MAX_LOG_LINES = 2000


def append_bounded_text_lines(
    widget: tk.Text,
    lines: Sequence[str],
    *,
    max_lines: int = DEFAULT_MAX_LOG_LINES,
) -> None:
    """Append lines to a text widget while keeping only the most recent rows."""

    if not lines:
        return
    widget.configure(state="normal")
    widget.insert("end", "".join(f"{line}\n" for line in lines))
    line_count = int(widget.index("end-1c").split(".")[0])
    overflow = max(0, line_count - max_lines)
    if overflow > 0:
        widget.delete("1.0", f"{overflow + 1}.0")
    widget.see("end")
    widget.configure(state="disabled")
