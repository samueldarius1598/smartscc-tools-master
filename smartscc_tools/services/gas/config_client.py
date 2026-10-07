"""GAS-backed Odoo configuration surface."""

from __future__ import annotations

from smartscc_tools.services.odoo.gateway import (
    CONFIG_GID,
    FALLBACK_CONFIG_SSID,
    GAS_API_KEY,
    GAS_BASE_URL,
    OdooConfig,
    fetch_odoo_config,
)

__all__ = [
    "CONFIG_GID",
    "FALLBACK_CONFIG_SSID",
    "GAS_API_KEY",
    "GAS_BASE_URL",
    "OdooConfig",
    "fetch_odoo_config",
]

