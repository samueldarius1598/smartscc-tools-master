"""Authentication service stub with non-blocking profile hydration."""

from __future__ import annotations

import abc
from dataclasses import dataclass
import logging
import threading
from typing import Callable


def build_runtime_settings(*args, **kwargs):
    from smartscc_tools.features.item_journal.config import build_runtime_settings as _build_runtime_settings

    return _build_runtime_settings(*args, **kwargs)


def fetch_odoo_config(*args, **kwargs):
    from smartscc_tools.services.odoo.gateway import fetch_odoo_config as _fetch_odoo_config

    return _fetch_odoo_config(*args, **kwargs)


@dataclass(frozen=True)
class AuthInfo:
    user_email: str
    display_name: str
    is_authenticated: bool
    auth_method: str  # "auto" | "login" | "api_key"


class AuthService(abc.ABC):
    @abc.abstractmethod
    def authenticate(self) -> AuthInfo:
        ...

    @abc.abstractmethod
    def get_current_user(self) -> AuthInfo | None:
        ...

    @abc.abstractmethod
    def logout(self) -> None:
        ...


class AutoAuthService(AuthService):
    """Return a placeholder user immediately, then hydrate from Odoo config."""

    def __init__(self, user_email: str = "", logger: logging.Logger | None = None) -> None:
        self._current: AuthInfo | None = None
        self._default_email = user_email
        self._logger = logger or logging.getLogger("smartscc_tools.auth")
        self._lock = threading.Lock()
        self._hydration_thread: threading.Thread | None = None

    def authenticate(self) -> AuthInfo:
        with self._lock:
            if self._current is not None:
                return self._current
            email = self._default_email or "user@smartscc.com"
            self._current = self._build_auth_info(email=email)
            return self._current

    def get_current_user(self) -> AuthInfo | None:
        with self._lock:
            return self._current

    def logout(self) -> None:
        with self._lock:
            self._current = None

    def refresh_from_odoo_config(self) -> AuthInfo:
        settings, _ = build_runtime_settings(preset="safe-fast", set_args=[])
        config = fetch_odoo_config(settings=settings)
        info = self._build_auth_info(email=config.user_email)
        with self._lock:
            self._current = info
        return info

    def start_background_hydration(self, on_update: Callable[[AuthInfo], None] | None = None) -> None:
        thread = self._hydration_thread
        if thread is not None and thread.is_alive():
            return

        def worker() -> None:
            try:
                info = self.refresh_from_odoo_config()
            except Exception as exc:  # noqa: BLE001
                self._logger.warning("Auth hydration skipped: %s", exc)
                return
            if on_update is None:
                return
            try:
                on_update(info)
            except Exception:  # noqa: BLE001
                self._logger.exception("Auth hydration callback failed")

        self._hydration_thread = threading.Thread(
            target=worker,
            name="smartscc-auth-hydration",
            daemon=True,
        )
        self._hydration_thread.start()

    @staticmethod
    def _build_auth_info(email: str) -> AuthInfo:
        clean_email = (email or "user@smartscc.com").strip() or "user@smartscc.com"
        display_name = clean_email.split("@")[0] if "@" in clean_email else clean_email
        return AuthInfo(
            user_email=clean_email,
            display_name=display_name,
            is_authenticated=True,
            auth_method="auto",
        )
