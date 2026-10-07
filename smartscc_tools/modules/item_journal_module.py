"""Module adapter: wraps Item Journal as an embeddable panel with global state."""

from __future__ import annotations

from functools import lru_cache
import tkinter as tk

from smartscc_tools.branding import ITEM_JOURNAL_ICON
from smartscc_tools.services.odoo.profiles import (
    FOLLOW_GLOBAL_PROFILE_ID,
    ensure_database_profile,
    normalize_module_database_profile_id,
)
from smartscc_tools.core.global_config import GlobalPersistentState, GlobalSettings
from smartscc_tools.core.module_base import ModuleBase, ModuleContext


@lru_cache(maxsize=1)
def _load_item_journal_gui_module():
    from smartscc_tools.features.item_journal.entrypoints import gui

    return gui


class ItemJournalModuleStateStore:
    """Bridge Item Journal state into the platform-wide global settings file."""

    def __init__(
        self,
        global_settings: GlobalSettings,
        global_state_store: GlobalPersistentState | None = None,
        legacy_state_store: object | None = None,
    ) -> None:
        self._global_settings = global_settings
        self._global_state_store = global_state_store or GlobalPersistentState()
        if legacy_state_store is None:
            legacy_state_store = _load_item_journal_gui_module().PersistentState()
        self._legacy_state_store = legacy_state_store

    def load(self):
        module_payload = self._global_settings.module_settings.get("item_journal")
        output_dir_text = ""
        gui_module = _load_item_journal_gui_module()
        if isinstance(module_payload, dict) and module_payload:
            output_dir_text = str(module_payload.get("output_dir") or "").strip()
            state = gui_module.gui_state_from_mapping(module_payload)
        else:
            state = self._legacy_state_store.load()
            self.save(state)

        if not output_dir_text and self._global_settings.default_output_dir.strip():
            state.output_dir = self._global_settings.default_output_dir.strip()
        state.database_profile_id = self._migrate_database_profile_id(module_payload, state)
        return state

    def save(self, state) -> None:
        gui_module = _load_item_journal_gui_module()
        self._global_settings.module_settings["item_journal"] = gui_module.gui_state_to_mapping(state)
        self._global_state_store.save(self._global_settings)

    def _migrate_database_profile_id(self, module_payload: object, state) -> str:
        if isinstance(module_payload, dict):
            current_value = module_payload.get("database_profile_id")
            current_profile_id = normalize_module_database_profile_id(current_value)
            if current_profile_id != state.database_profile_id or str(current_value or "").strip():
                return current_profile_id

            legacy_db_override = str(module_payload.get("db_override") or "").strip()
            if legacy_db_override:
                return ensure_database_profile(
                    self._global_settings.database_profiles,
                    legacy_db_override,
                    alias=legacy_db_override,
                )

        if normalize_module_database_profile_id(state.database_profile_id) != state.database_profile_id:
            return normalize_module_database_profile_id(state.database_profile_id)
        if state.database_profile_id and state.database_profile_id != FOLLOW_GLOBAL_PROFILE_ID:
            return ensure_database_profile(
                self._global_settings.database_profiles,
                state.database_profile_id,
                alias=state.database_profile_id,
            )
        return normalize_module_database_profile_id(state.database_profile_id)


class ItemJournalModule(ModuleBase):

    @property
    def module_id(self) -> str:
        return "item_journal"

    @property
    def display_name(self) -> str:
        return "Internal Transfer - Odoo"

    @property
    def description(self) -> str:
        return "Upload internal transfer dari Excel ke Odoo"

    @property
    def icon_path(self) -> str | None:
        return str(ITEM_JOURNAL_ICON) if ITEM_JOURNAL_ICON.exists() else None

    def create_ui(self, parent: tk.Frame, context: ModuleContext) -> tk.Frame:
        state_store = ItemJournalModuleStateStore(global_settings=context.global_settings)
        panel_cls = _load_item_journal_gui_module().ItemJournalPanel
        self._panel = panel_cls(
            parent=parent,
            state_store=state_store,
            global_settings=context.global_settings,
            master_cache=context.master_cache,
        )
        return parent

    def on_activate(self) -> None:
        if hasattr(self, "_panel"):
            self._panel.resume()
            self._panel.refresh_database_options()

    def on_deactivate(self) -> None:
        if hasattr(self, "_panel"):
            self._panel.pause()

    def on_shutdown(self) -> None:
        if hasattr(self, "_panel"):
            self._panel.shutdown()
