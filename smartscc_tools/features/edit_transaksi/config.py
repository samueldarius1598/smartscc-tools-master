"""Configuration for Edit Transaksi module."""

from __future__ import annotations

from dataclasses import dataclass

from smartscc_tools.services.odoo.profiles import FOLLOW_GLOBAL_PROFILE_ID


@dataclass
class EditTransaksiSettings:
    http_timeout_read: int = 30
    max_retry: int = 2
    database_profile_id: str = FOLLOW_GLOBAL_PROFILE_ID
    logs_section_open: bool = False
