"""Odoo-inspired color palette, fonts, and UI constants."""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Brand colors (Odoo-inspired)
# ---------------------------------------------------------------------------
BRAND_PRIMARY = "#714B67"
BRAND_PRIMARY_DARK = "#5B3D54"
BRAND_SECONDARY = "#00A09D"
BRAND_ACCENT = "#017E84"

# ---------------------------------------------------------------------------
# Backgrounds
# ---------------------------------------------------------------------------
BG_MAIN = "#F8F9FA"
BG_SIDEBAR = "#2C2C3E"
BG_SIDEBAR_HOVER = "#3A3A50"
BG_SIDEBAR_ACTIVE = "#3A3A50"
BG_HEADER = "#714B67"
BG_FOOTER = "#2C2C3E"
BG_CARD = "#FFFFFF"
BG_INPUT = "#FFFFFF"

# ---------------------------------------------------------------------------
# Text
# ---------------------------------------------------------------------------
TEXT_ON_DARK = "#FFFFFF"
TEXT_ON_LIGHT = "#2C2C3E"
TEXT_MUTED = "#8C8C8C"
TEXT_SIDEBAR = "#B8B8CC"
TEXT_SIDEBAR_ACTIVE = "#FFFFFF"

# ---------------------------------------------------------------------------
# Status / semantic
# ---------------------------------------------------------------------------
STATUS_SUCCESS = "#21B573"
STATUS_WARNING = "#F0AD4E"
STATUS_ERROR = "#E5483E"
STATUS_INFO = "#00A09D"

# ---------------------------------------------------------------------------
# Borders
# ---------------------------------------------------------------------------
BORDER_LIGHT = "#DEE2E6"
BORDER_FOCUS = "#714B67"

# ---------------------------------------------------------------------------
# Typography
# ---------------------------------------------------------------------------
FONT_FAMILY = "Segoe UI"
FONT_FALLBACKS = ("Helvetica", "Arial", "sans-serif")
FONT_HEADING_SIZE = 14
FONT_BODY_SIZE = 10
FONT_SMALL_SIZE = 9
FONT_FOOTER_SIZE = 8

# ---------------------------------------------------------------------------
# Layout constants
# ---------------------------------------------------------------------------
HEADER_HEIGHT = 56
FOOTER_HEIGHT = 28
SIDEBAR_WIDTH = 210
SIDEBAR_ITEM_HEIGHT = 44
CARD_PADDING = 16
CARD_BORDER_RADIUS = 8
MIN_LOG_VISIBLE_LINES = 15

FOOTER_TEXT = "CC-HWG"


def font(size: int = FONT_BODY_SIZE, bold: bool = False) -> tuple[str, int, str]:
    weight = "bold" if bold else "normal"
    return (FONT_FAMILY, size, weight)
