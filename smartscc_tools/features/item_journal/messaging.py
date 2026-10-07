"""Canonical user-facing messaging and narration helpers."""

from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Any, Mapping, Sequence

from smartscc_tools.features.item_journal.utils import normalize_text, to_int


BUSINESS_MESSAGES: dict[str, str] = {
    "CONNECTED_TO_ODOO": "Terhubung ke Odoo dengan sukses.",
    "UPLOAD_STARTED": "Proses upload Internal Transfer dimulai.",
    "UPLOAD_COMPLETED": "Upload selesai. Periksa Excel Hasil Upload.",
    "JOURNAL_VERIFIED": "No. Jurnal Akuntansi berhasil diterbitkan: {journal_ref}.",
    "JOURNAL_SUMMARY": (
        "Ringkasan Jurnal Akuntansi: "
        "Baris berhasil={row_ok}, gagal={row_failed}, tanpa jurnal={row_not_required}, lainnya={row_other} | "
        "Transfer berhasil={transfer_ok}, gagal={transfer_failed}, tanpa jurnal={transfer_not_required}, lainnya={transfer_other}."
    ),
    "JOURNAL_WAITING": "Sedang menunggu konfirmasi Jurnal dari Odoo...",
    "JOURNAL_NOT_REQUIRED": (
        "Transfer berhasil. Tidak ada Jurnal yang terbentuk "
        "(sesuai konfigurasi Produk/Lokasi)."
    ),
    "JOURNAL_TIMEOUT_ERROR": (
        "Transfer berhasil, tetapi Jurnal belum muncul setelah menunggu lama. "
        "Silakan cek manual di Odoo."
    ),
    "JOURNAL_GENERIC_WARNING": "Status jurnal belum pasti (status={journal_status}).",
}

STAGE_MESSAGES: dict[str, str] = {
    "AUTH_CHECK": "Menghubungkan ke Odoo...",
    "DATE_SYNC_PREFLIGHT": "Menyiapkan sinkronisasi tanggal...",
    "GLOBAL_PRECHECK": "Memeriksa data Excel sebelum upload...",
    "PREFETCH": "Menyiapkan master produk dan UoM...",
    "PICKING_CREATE": "Membuat draft transfer stok...",
    "PICKING_VALIDATE_START": "Memvalidasi perpindahan stok dan posting jurnal...",
    "PICKING_VALIDATE_DONE": "Validasi transfer selesai.",
    "RUN_DONE": "Upload selesai. Periksa Excel Hasil Upload.",
}


def _safe_format(template: str, context: Mapping[str, Any]) -> str:
    try:
        return template.format(**context)
    except Exception:
        return template


@dataclass(frozen=True)
class JournalNarrationContext:
    journal_status: str
    journal_refs: Sequence[str]
    journal_reason: str = ""
    has_timeout_warning: bool = False


@dataclass(frozen=True)
class SlowFeedback:
    level: str
    message: str


class BusinessNarrator:
    """Separate user-facing narration from technical telemetry."""

    def __init__(
        self,
        user_logger: logging.Logger,
        technical_logger: logging.Logger | None = None,
    ) -> None:
        self.user_logger = user_logger
        self.technical_logger = technical_logger or user_logger

    def render(self, key: str, **context: Any) -> str:
        template = BUSINESS_MESSAGES.get(key, key)
        return _safe_format(template, context)

    def info(self, key: str, **context: Any) -> str:
        message = self.render(key, **context)
        self.user_logger.info(message)
        return message

    def warning(self, key: str, **context: Any) -> str:
        message = self.render(key, **context)
        self.user_logger.warning(message)
        return message

    def error(self, key: str, **context: Any) -> str:
        message = self.render(key, **context)
        self.user_logger.error(message)
        return message

    def technical(self, event_key: str, **details: Any) -> None:
        if not self.technical_logger.isEnabledFor(logging.DEBUG):
            return
        payload = ", ".join(f"{key}={details[key]!r}" for key in sorted(details))
        self.technical_logger.debug("TECH[%s] %s", event_key, payload)

    def narrate_journal_outcome(self, context: JournalNarrationContext) -> str:
        status = str(context.journal_status or "").strip().lower()

        if status == "ok":
            journal_ref = str(context.journal_refs[0]) if context.journal_refs else "-"
            return self.info("JOURNAL_VERIFIED", journal_ref=journal_ref)

        if status in {"not_expected", "cost_zero_exception"}:
            return self.info("JOURNAL_NOT_REQUIRED")

        if bool(context.has_timeout_warning):
            return self.warning("JOURNAL_TIMEOUT_ERROR")

        if status in {"pending", "stopped"}:
            return self.info("JOURNAL_WAITING")

        return self.warning("JOURNAL_GENERIC_WARNING", journal_status=status or "unknown")


def _default_message_from_context(context: Mapping[str, Any]) -> str:
    return normalize_text(context.get("message", "")) or "Memproses data upload..."


def _format_qty(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return normalize_text(value)
    if number.is_integer():
        return str(int(number))
    return f"{number:.2f}".rstrip("0").rstrip(".")


def _normalize_preview_items(items: Any) -> list[str]:
    if isinstance(items, (str, bytes)) or not isinstance(items, Sequence):
        return []
    normalized: list[str] = []
    for item in items:
        if isinstance(item, Mapping):
            name = normalize_text(item.get("name")) or "item"
            qty = _format_qty(item.get("qty"))
            if qty:
                normalized.append(f"{name} x{qty}")
            else:
                normalized.append(name)
            continue
        text = normalize_text(item)
        if text:
            normalized.append(text)
    return normalized


def _join_preview(items: Sequence[str]) -> str:
    return "; ".join(item for item in items if normalize_text(item))


def build_sync_wait_message(base_message: str, context: Mapping[str, Any] | None = None) -> str:
    payload = context or {}
    base = normalize_text(base_message) or "Sedang menyinkronkan data besar, mohon tunggu sebentar..."
    company = normalize_text(payload.get("company_name")) or "-"
    src = normalize_text(payload.get("src")) or "-"
    dest = normalize_text(payload.get("dest")) or "-"
    count = max(0, to_int(payload.get("item_count")))
    preview_top = _normalize_preview_items(payload.get("preview_top"))
    preview_bottom = _normalize_preview_items(payload.get("preview_bottom"))

    detail = f"Company={company} | Rute={src} -> {dest} | Item={count}"
    top_text = _join_preview(preview_top)
    bottom_text = _join_preview(preview_bottom)
    if top_text and bottom_text:
        return f"{base} {detail} | Top 5: {top_text} | ... | Bottom 5: {bottom_text}"
    if top_text:
        return f"{base} {detail} | Item: {top_text}"
    return f"{base} {detail}"


def build_slow_feedback(elapsed_sec: int) -> SlowFeedback | None:
    elapsed = max(0, int(elapsed_sec or 0))
    if elapsed < 10:
        return None
    if elapsed < 30:
        return SlowFeedback(
            level="info",
            message="Sedang menyinkronkan data besar, mohon tunggu sebentar...",
        )
    return SlowFeedback(
        level="warn",
        message="Koneksi Odoo lambat, mencoba kembali...",
    )


def stage_to_business_message(stage: str, context: Mapping[str, Any] | None = None) -> str:
    payload = context or {}
    clean_stage = normalize_text(stage).upper()

    if clean_stage == "TRANSFER_START":
        company = normalize_text(payload.get("company_name")) or "Company"
        count = max(1, to_int(payload.get("item_count")))
        return f"Memproses Transfer Internal untuk {company} ({count} item)..."

    if clean_stage == "TRANSFER_PROGRESS":
        company = normalize_text(payload.get("company_name")) or "Company"
        count = max(1, to_int(payload.get("item_count")))
        return f"Memproses Transfer Internal untuk {company} ({count} item)..."

    if clean_stage == "TRANSFER_DONE":
        transfer_ref = normalize_text(payload.get("transfer_ref")) or "-"
        return f"Transfer {transfer_ref} berhasil diproses."

    if clean_stage == "TRANSFER_ERROR":
        row_number = to_int(payload.get("row_number"))
        product_name = normalize_text(payload.get("product_name")) or "produk"
        base = normalize_text(payload.get("error_summary")) or normalize_text(payload.get("message"))
        if row_number > 0:
            if base:
                return f"Gagal memproses {product_name} (baris {row_number}): {base}"
            return f"Gagal memproses {product_name} (baris {row_number})."
        if base:
            return f"Gagal memproses {product_name}: {base}"
        return f"Gagal memproses {product_name}."

    if clean_stage == "SYNC_WAIT":
        return build_sync_wait_message(
            base_message=normalize_text(payload.get("message")),
            context=payload,
        )

    if clean_stage in STAGE_MESSAGES:
        return STAGE_MESSAGES[clean_stage]

    if clean_stage.startswith("PICKING_VALIDATE"):
        return STAGE_MESSAGES["PICKING_VALIDATE_START"]

    return _default_message_from_context(payload)
