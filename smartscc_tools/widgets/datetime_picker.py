"""Reusable date-time picker built around tkcalendar + readonly time selectors."""

from __future__ import annotations

from datetime import datetime
import tkinter as tk
from tkinter import ttk

from smartscc_tools.core import theme as T

try:
    from tkcalendar import DateEntry
except Exception:  # noqa: BLE001
    DateEntry = None  # type: ignore[assignment]


class DateTimePickerField(tk.Frame):
    """Readonly date picker with HH:MM:SS selectors."""

    def __init__(
        self,
        parent: tk.Widget,
        *,
        label: str,
        show_time: bool = True,
        fallback_text: str = "tkcalendar belum tersedia",
    ) -> None:
        super().__init__(parent, bg=T.BG_CARD)
        self._show_time = show_time
        self._fallback_text = fallback_text
        self._enabled = True
        self._date_var = tk.StringVar()
        self._hour_var = tk.StringVar(value="00")
        self._minute_var = tk.StringVar(value="00")
        self._second_var = tk.StringVar(value="00")
        self._fallback_var = tk.StringVar(value=fallback_text)
        self._date_widget = None
        self._fallback_entry: ttk.Entry | None = None
        self._time_pickers: list[ttk.Combobox] = []
        self._calendar_focus_after_id: str | None = None

        self._label = tk.Label(
            self,
            text=label,
            bg=T.BG_CARD,
            fg=T.TEXT_ON_LIGHT,
            font=T.font(T.FONT_BODY_SIZE),
        )
        self._label.pack(anchor="w", pady=(0, 4))

        control_row = tk.Frame(self, bg=T.BG_CARD)
        control_row.pack(anchor="w")

        if DateEntry is not None:
            try:
                date_widget = DateEntry(
                    control_row,
                    width=12,
                    date_pattern="yyyy-mm-dd",
                    state="readonly",
                    background=T.BRAND_PRIMARY,
                    foreground=T.TEXT_ON_DARK,
                    borderwidth=1,
                )
                date_widget.pack(side="left")
                self._date_widget = date_widget
                self._install_calendar_focus_guard()
            except Exception:  # noqa: BLE001
                self._date_widget = None

        if self._date_widget is None:
            self._fallback_entry = ttk.Entry(control_row, textvariable=self._fallback_var, width=16, state="readonly")
            self._fallback_entry.pack(side="left")

        if show_time:
            tk.Label(
                control_row,
                text="  ",
                bg=T.BG_CARD,
                fg=T.TEXT_ON_LIGHT,
            ).pack(side="left")
            for var, values in (
                (self._hour_var, self._build_value_list(24)),
                (self._minute_var, self._build_value_list(60)),
                (self._second_var, self._build_value_list(60)),
            ):
                picker = ttk.Combobox(
                    control_row,
                    textvariable=var,
                    values=values,
                    width=3,
                    state="readonly",
                )
                self._time_pickers.append(picker)
                picker.pack(side="left")
                if var is not self._second_var:
                    tk.Label(control_row, text=":", bg=T.BG_CARD, fg=T.TEXT_ON_LIGHT).pack(side="left", padx=2)

    @staticmethod
    def _build_value_list(limit: int) -> tuple[str, ...]:
        return tuple(f"{value:02d}" for value in range(limit))

    def set_value(self, raw_value: str) -> None:
        self._withdraw_calendar_popup()
        parsed = self.parse_value(raw_value)
        if parsed is None:
            if self._date_widget is None:
                self._fallback_var.set(self._fallback_text if not raw_value else str(raw_value))
            return

        date_text, hour_text, minute_text, second_text = parsed
        self._hour_var.set(hour_text)
        self._minute_var.set(minute_text)
        self._second_var.set(second_text)
        if self._date_widget is not None:
            self._set_date_widget_value(date_text)
        else:
            self._fallback_var.set(date_text)

    def clear(self) -> None:
        self._withdraw_calendar_popup()
        if self._date_widget is not None:
            self._set_date_widget_value(datetime.now().date())
        else:
            self._fallback_var.set(self._fallback_text)
        self._hour_var.set("00")
        self._minute_var.set("00")
        self._second_var.set("00")

    def get_value(self, *, date_only: bool = False) -> str:
        date_text = ""
        if self._date_widget is not None:
            try:
                date_value = self._date_widget.get_date()
                date_text = date_value.strftime("%Y-%m-%d")
            except Exception:  # noqa: BLE001
                date_text = ""
        else:
            fallback_text = str(self._fallback_var.get() or "").strip()
            parsed = self.parse_value(fallback_text)
            date_text = parsed[0] if parsed else ""
        return self.compose_value(
            date_text=date_text,
            hour_text=self._hour_var.get(),
            minute_text=self._minute_var.get(),
            second_text=self._second_var.get(),
            date_only=date_only,
        )

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = bool(enabled)
        self._label.configure(fg=T.TEXT_ON_LIGHT if enabled else T.TEXT_MUTED)
        self._withdraw_calendar_popup()

        if self._date_widget is not None:
            self._apply_ttk_readonly_state(self._date_widget, enabled=enabled)
        if self._fallback_entry is not None:
            try:
                self._fallback_entry.configure(state="readonly" if enabled else "disabled")
            except Exception:  # noqa: BLE001
                pass
        for picker in self._time_pickers:
            self._apply_ttk_readonly_state(picker, enabled=enabled)

    def _set_date_widget_value(self, value: object) -> None:
        if self._date_widget is None:
            return
        self._run_with_date_widget_write_access(lambda: self._date_widget.set_date(value))

    def _run_with_date_widget_write_access(self, callback) -> None:
        if self._date_widget is None:
            return
        self._withdraw_calendar_popup()
        self._apply_ttk_readonly_state(self._date_widget, enabled=True)
        try:
            callback()
        except Exception:  # noqa: BLE001
            pass
        finally:
            self._apply_ttk_readonly_state(self._date_widget, enabled=self._enabled)

    def _install_calendar_focus_guard(self) -> None:
        calendar = self._calendar_widget()
        if calendar is None:
            return
        try:
            calendar.bind("<FocusOut>", self._on_calendar_focus_out)
        except Exception:  # noqa: BLE001
            pass

    def _on_calendar_focus_out(self, _event: object) -> None:
        calendar = self._calendar_widget()
        if calendar is None:
            return
        self._cancel_calendar_focus_resolution()
        try:
            self._calendar_focus_after_id = calendar.after_idle(self._finalize_calendar_focus_out)
        except Exception:  # noqa: BLE001
            self._calendar_focus_after_id = None
            self._finalize_calendar_focus_out()

    def _cancel_calendar_focus_resolution(self) -> None:
        after_id = self._calendar_focus_after_id
        self._calendar_focus_after_id = None
        if not after_id:
            return
        calendar = self._calendar_widget()
        if calendar is None:
            return
        try:
            calendar.after_cancel(after_id)
        except Exception:  # noqa: BLE001
            pass

    def _finalize_calendar_focus_out(self) -> None:
        self._calendar_focus_after_id = None
        calendar = self._calendar_widget()
        top_cal = self._calendar_popup()
        if calendar is None or top_cal is None or not self._is_popup_visible(top_cal):
            return

        focus_widget = None
        try:
            focus_widget = self.focus_get()
        except Exception:  # noqa: BLE001
            focus_widget = None

        if self._widget_within_popup(focus_widget, top_cal):
            if focus_widget is not calendar:
                try:
                    calendar.focus_force()
                except Exception:  # noqa: BLE001
                    pass
            return

        if self._pointer_inside_popup(top_cal):
            try:
                calendar.focus_force()
            except Exception:  # noqa: BLE001
                pass
            return

        self._close_calendar_popup()

    def _close_calendar_popup(self) -> None:
        top_cal = self._calendar_popup()
        if top_cal is not None:
            try:
                if top_cal.winfo_exists() and top_cal.winfo_ismapped():
                    top_cal.withdraw()
            except Exception:  # noqa: BLE001
                pass
        if self._date_widget is None:
            return
        state_method = getattr(self._date_widget, "state", None)
        if callable(state_method):
            try:
                state_method(("!pressed",))
            except Exception:  # noqa: BLE001
                pass

    def _calendar_widget(self) -> object | None:
        if self._date_widget is None:
            return None
        return getattr(self._date_widget, "_calendar", None)

    def _calendar_popup(self) -> object | None:
        if self._date_widget is None:
            return None
        return getattr(self._date_widget, "_top_cal", None)

    @staticmethod
    def _widget_within_popup(widget: object | None, popup: object) -> bool:
        if widget is None:
            return False
        top_level_method = getattr(widget, "winfo_toplevel", None)
        if not callable(top_level_method):
            return False
        try:
            return top_level_method() == popup
        except Exception:  # noqa: BLE001
            return False

    @staticmethod
    def _is_popup_visible(popup: object) -> bool:
        try:
            return bool(popup.winfo_exists() and popup.winfo_ismapped())
        except Exception:  # noqa: BLE001
            return False

    @staticmethod
    def _pointer_inside_popup(popup: object) -> bool:
        if not DateTimePickerField._is_popup_visible(popup):
            return False
        try:
            pointer_x, pointer_y = popup.winfo_pointerxy()
            origin_x = popup.winfo_rootx()
            origin_y = popup.winfo_rooty()
            width = popup.winfo_width()
            height = popup.winfo_height()
        except Exception:  # noqa: BLE001
            return False
        return origin_x <= pointer_x <= origin_x + width and origin_y <= pointer_y <= origin_y + height

    def _withdraw_calendar_popup(self) -> None:
        self._cancel_calendar_focus_resolution()
        self._close_calendar_popup()

    @staticmethod
    def _apply_ttk_readonly_state(widget: object, *, enabled: bool) -> None:
        state_method = getattr(widget, "state", None)
        if not callable(state_method):
            return
        try:
            if enabled:
                state_method(("!disabled", "readonly"))
            else:
                state_method(("disabled", "!readonly"))
        except Exception:  # noqa: BLE001
            pass

    @staticmethod
    def parse_value(raw_value: str) -> tuple[str, str, str, str] | None:
        clean = str(raw_value or "").strip().replace("T", " ")
        if not clean:
            return None
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
            try:
                parsed = datetime.strptime(clean, fmt)
            except ValueError:
                continue
            return (
                parsed.strftime("%Y-%m-%d"),
                parsed.strftime("%H"),
                parsed.strftime("%M"),
                parsed.strftime("%S"),
            )
        raise ValueError("Format tanggal harus YYYY-MM-DD atau YYYY-MM-DD HH:MM[:SS].")

    @staticmethod
    def compose_value(
        *,
        date_text: str,
        hour_text: str,
        minute_text: str,
        second_text: str,
        date_only: bool = False,
    ) -> str:
        clean_date = str(date_text or "").strip()
        if not clean_date:
            return ""
        if date_only:
            datetime.strptime(clean_date, "%Y-%m-%d")
            return clean_date
        hour = int(hour_text or 0)
        minute = int(minute_text or 0)
        second = int(second_text or 0)
        return f"{clean_date} {hour:02d}:{minute:02d}:{second:02d}"
