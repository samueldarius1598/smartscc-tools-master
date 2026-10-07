"""Abstract base class for platform modules."""

from __future__ import annotations

import abc
import logging
import tkinter as tk
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class ModuleContext:
    """Shared context passed to every module on UI creation."""

    global_settings: Any
    auth_info: Any
    logger: logging.Logger
    technical_logger: logging.Logger
    root: tk.Tk
    status_callback: Callable[[str], None] = field(default=lambda msg: None)
    master_cache: Any | None = None


class ModuleBase(abc.ABC):
    """Contract that every module must implement."""

    @property
    @abc.abstractmethod
    def module_id(self) -> str:
        """Unique slug, e.g. 'item_journal'."""

    @property
    @abc.abstractmethod
    def display_name(self) -> str:
        """Human-readable name shown in sidebar."""

    @property
    @abc.abstractmethod
    def description(self) -> str:
        """One-line description shown in dashboard."""

    @property
    def icon_path(self) -> str | None:
        """Optional path to module icon PNG."""
        return None

    @abc.abstractmethod
    def create_ui(self, parent: tk.Frame, context: ModuleContext) -> tk.Frame:
        """Build and return the module's UI frame."""

    def on_activate(self) -> None:
        """Called when module is shown."""

    def on_deactivate(self) -> None:
        """Called when user navigates away."""

    def on_shutdown(self) -> None:
        """Cleanup when app closes."""
