"""Header bar: logo, title, profile area."""

from __future__ import annotations

import tkinter as tk

from smartscc_tools import APP_NAME
from smartscc_tools.branding import MAIN_LOGO_PNG
from smartscc_tools.core import theme as T
from smartscc_tools.core.auth import AuthInfo
from smartscc_tools.widgets.image_utils import load_icon


class HeaderWidget(tk.Frame):
    """Purple header bar with logo, title, and profile."""

    def __init__(self, parent: tk.Widget, auth_info: AuthInfo | None = None) -> None:
        super().__init__(parent, bg=T.BG_HEADER, height=T.HEADER_HEIGHT)
        self.pack_propagate(False)
        self._display_name_var = tk.StringVar()
        self._email_var = tk.StringVar()

        # Left side: logo + title
        left = tk.Frame(self, bg=T.BG_HEADER)
        left.pack(side="left", fill="y", padx=(16, 0))

        self._logo_image = None
        logo_path = MAIN_LOGO_PNG
        if logo_path.exists():
            try:
                self._logo_image = load_icon(logo_path, target_height=36, master=self)
                if self._logo_image is not None:
                    tk.Label(left, image=self._logo_image, bg=T.BG_HEADER).pack(side="left", padx=(0, 10))
            except (tk.TclError, RuntimeError):
                pass

        tk.Label(
            left, text=APP_NAME,
            bg=T.BG_HEADER, fg=T.TEXT_ON_DARK,
            font=T.font(T.FONT_HEADING_SIZE, bold=True),
        ).pack(side="left")

        # Right side: profile
        right = tk.Frame(self, bg=T.BG_HEADER)
        right.pack(side="right", fill="y", padx=(0, 16))

        display_name = auth_info.display_name if auth_info else "User"
        email = auth_info.user_email if auth_info else ""

        # Avatar placeholder (circle with initial)
        avatar_size = 32
        self._avatar_canvas = tk.Canvas(
            right, width=avatar_size, height=avatar_size,
            bg=T.BG_HEADER, highlightthickness=0,
        )
        self._avatar_canvas.pack(side="left", padx=(0, 8), pady=12)
        self._avatar_canvas.create_oval(2, 2, avatar_size - 2, avatar_size - 2,
                                        fill=T.BRAND_SECONDARY, outline="")
        self._avatar_text_id = self._avatar_canvas.create_text(
            avatar_size // 2,
            avatar_size // 2,
            text="U",
            fill=T.TEXT_ON_DARK,
            font=T.font(12, bold=True),
        )

        profile_text = tk.Frame(right, bg=T.BG_HEADER)
        profile_text.pack(side="left", fill="y")
        tk.Label(
            profile_text,
            textvariable=self._display_name_var,
            bg=T.BG_HEADER,
            fg=T.TEXT_ON_DARK,
            font=T.font(T.FONT_BODY_SIZE, bold=True),
        ).pack(anchor="w", pady=(10, 0))
        tk.Label(
            profile_text,
            textvariable=self._email_var,
            bg=T.BG_HEADER,
            fg="#D4C4CF",
            font=T.font(T.FONT_FOOTER_SIZE),
        ).pack(anchor="w")
        self.update_auth_info(auth_info)

    def update_auth_info(self, auth_info: AuthInfo | None) -> None:
        display_name = auth_info.display_name if auth_info and auth_info.display_name else "User"
        email = auth_info.user_email if auth_info else ""
        self._display_name_var.set(display_name)
        self._email_var.set(email)
        initial = display_name[0].upper() if display_name else "U"
        self._avatar_canvas.itemconfigure(self._avatar_text_id, text=initial)
