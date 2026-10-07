"""Reusable collapsible card widget for Smart's CC Tools Master."""

from __future__ import annotations

import tkinter as tk
from typing import Callable

from smartscc_tools.core import theme as T


def build_compact_preview_text(
    text: str,
    *,
    empty_text: str = "",
    max_chars: int = 120,
) -> str:
    """Collapse multi-line or long text into a single-line preview."""

    clean = " ".join(str(text or "").split())
    if not clean:
        return empty_text
    if len(clean) <= max_chars:
        return clean
    if max_chars <= 3:
        return "." * max(0, max_chars)
    return clean[: max_chars - 3].rstrip() + "..."


class CollapsibleSection(tk.Frame):
    def __init__(
        self,
        parent: tk.Misc,
        *,
        key: str,
        title: str,
        expanded: bool,
        on_toggle: Callable[[str, bool], None] | None = None,
        body_fill: str = "x",
        body_expand: bool = False,
        compact_fill: str = "x",
        compact_expand: bool = False,
        show_compact_when_open: bool = False,
        summary_text: str = "",
        summary_fg: str = T.TEXT_MUTED,
    ) -> None:
        super().__init__(
            parent,
            bg=T.BG_CARD,
            bd=1,
            relief="solid",
            highlightbackground=T.BORDER_LIGHT,
            highlightthickness=1,
        )
        self.key = key
        self.title = title
        self._open = bool(expanded)
        self._on_toggle = on_toggle
        self._body_pack = {"fill": body_fill, "expand": body_expand}
        self._compact_pack = {"fill": compact_fill, "expand": compact_expand}
        self._show_compact_when_open = bool(show_compact_when_open)
        self._summary_text = str(summary_text or "").strip()
        self._summary_fg = summary_fg

        self.header = tk.Frame(self, bg=T.BG_CARD, padx=12, pady=8)
        self.header.pack(fill="x")
        self.header.columnconfigure(2, weight=1)

        self.toggle_button = tk.Button(
            self.header,
            command=self.toggle,
            bg=T.BRAND_PRIMARY,
            fg=T.TEXT_ON_DARK,
            activebackground=T.BRAND_PRIMARY_DARK,
            activeforeground=T.TEXT_ON_DARK,
            disabledforeground=T.TEXT_ON_DARK,
            relief="flat",
            bd=0,
            highlightthickness=0,
            cursor="hand2",
            font=T.font(T.FONT_SMALL_SIZE, bold=True),
            padx=12,
            pady=4,
        )
        self.toggle_button.grid(row=0, column=0, sticky="w")

        self.title_label = tk.Label(
            self.header,
            text=title,
            bg=T.BG_CARD,
            fg=T.TEXT_ON_LIGHT,
            font=T.font(T.FONT_BODY_SIZE, bold=True),
        )
        self.title_label.grid(row=0, column=1, sticky="w", padx=(12, 0))

        self.summary_var = tk.StringVar(value="")
        self.summary_label = tk.Label(
            self.header,
            textvariable=self.summary_var,
            bg=T.BG_CARD,
            fg=self._summary_fg,
            font=T.font(T.FONT_SMALL_SIZE),
            anchor="e",
            justify="right",
        )
        self.summary_label.grid(row=0, column=2, sticky="e", padx=(12, 0))

        self.header_divider = tk.Frame(
            self,
            bg=T.BORDER_LIGHT,
            height=1,
        )

        self.compact_body = tk.Frame(self, bg=T.BG_CARD, padx=12, pady=8)
        self.body = tk.Frame(self, bg=T.BG_CARD, padx=12, pady=12)
        self._apply_layout()

    @property
    def is_open(self) -> bool:
        return self._open

    @property
    def summary_text(self) -> str:
        return self._summary_text

    def toggle(self) -> None:
        self.set_open(not self._open)

    def set_open(self, is_open: bool, *, emit: bool = True) -> None:
        self._open = bool(is_open)
        self._apply_layout()
        if emit and self._on_toggle is not None:
            self._on_toggle(self.key, self._open)

    def set_summary(self, text: str, *, fg: str | None = None) -> None:
        self._summary_text = str(text or "").strip()
        if fg is not None:
            self._summary_fg = fg
            self.summary_label.configure(fg=fg)
        self._apply_layout()

    def refresh_layout(self) -> None:
        self._apply_layout()

    def _apply_layout(self) -> None:
        has_compact = bool(self.compact_body.winfo_children())
        has_body = bool(self.body.winfo_children())
        show_summary = (not self._open) and bool(self._summary_text)
        has_visible_content = (self._open and (has_body or (has_compact and self._show_compact_when_open))) or (
            (not self._open) and has_compact
        )

        desired = (self._open, has_compact, has_body, has_visible_content, show_summary, self._summary_text)
        if getattr(self, "_layout_state", None) == desired:
            return
        self._layout_state = desired

        self.toggle_button.configure(text="Hide" if self._open else "Show More")
        self.summary_var.set(self._summary_text if show_summary else "")
        if show_summary:
            self.summary_label.grid()
        else:
            self.summary_label.grid_remove()

        self.header_divider.pack_forget()
        self.compact_body.pack_forget()
        self.body.pack_forget()

        if has_visible_content:
            self.header_divider.pack(fill="x")

        if self._open:
            if has_compact and self._show_compact_when_open:
                self.compact_body.pack(**self._compact_pack)
            if has_body:
                self.body.pack(**self._body_pack)
        else:
            if has_compact:
                self.compact_body.pack(**self._compact_pack)
