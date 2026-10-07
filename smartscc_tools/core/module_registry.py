"""Static module registry."""

from __future__ import annotations

import importlib

from smartscc_tools import branding
from smartscc_tools.core.module_base import ModuleBase


class LazyModuleProxy(ModuleBase):
    """Lightweight module metadata that loads the real module on first UI use."""

    def __init__(
        self,
        *,
        module_id: str,
        display_name: str,
        description: str,
        module_path: str,
        class_name: str,
        icon_path: str | None = None,
    ) -> None:
        self._module_id = module_id
        self._display_name = display_name
        self._description = description
        self._module_path = module_path
        self._class_name = class_name
        self._icon_path = icon_path
        self._loaded_module: ModuleBase | None = None

    @property
    def module_id(self) -> str:
        return self._module_id

    @property
    def display_name(self) -> str:
        return self._display_name

    @property
    def description(self) -> str:
        return self._description

    @property
    def icon_path(self) -> str | None:
        return self._icon_path

    def _load(self) -> ModuleBase:
        if self._loaded_module is None:
            module = importlib.import_module(self._module_path)
            module_cls = getattr(module, self._class_name)
            loaded = module_cls()
            if loaded.module_id != self._module_id:
                raise ValueError(
                    f"Lazy module id mismatch for {self._module_path}.{self._class_name}: "
                    f"expected {self._module_id}, got {loaded.module_id}"
                )
            self._loaded_module = loaded
        return self._loaded_module

    def create_ui(self, parent, context):
        return self._load().create_ui(parent, context)

    def on_activate(self) -> None:
        if self._loaded_module is not None:
            self._loaded_module.on_activate()

    def on_deactivate(self) -> None:
        if self._loaded_module is not None:
            self._loaded_module.on_deactivate()

    def on_shutdown(self) -> None:
        if self._loaded_module is not None:
            self._loaded_module.on_shutdown()


def _icon_path(path) -> str | None:
    return str(path) if path.exists() else None


class ModuleRegistry:
    """Discovers and stores available modules."""

    def __init__(self) -> None:
        self._modules: dict[str, ModuleBase] = {}
        self._order: list[str] = []

    def register(self, module: ModuleBase) -> None:
        if module.module_id in self._modules:
            raise ValueError(f"Module already registered: {module.module_id}")
        self._modules[module.module_id] = module
        self._order.append(module.module_id)

    def get(self, module_id: str) -> ModuleBase | None:
        return self._modules.get(module_id)

    def all_modules(self) -> list[ModuleBase]:
        return [self._modules[mid] for mid in self._order]

    @staticmethod
    def create_default() -> ModuleRegistry:
        registry = ModuleRegistry()
        registry.register(
            LazyModuleProxy(
                module_id="item_journal",
                display_name="Internal Transfer - Odoo",
                description="Upload internal transfer dari Excel ke Odoo",
                icon_path=_icon_path(branding.ITEM_JOURNAL_ICON),
                module_path="smartscc_tools.modules.item_journal_module",
                class_name="ItemJournalModule",
            )
        )
        registry.register(
            LazyModuleProxy(
                module_id="edit_transaksi",
                display_name="Edit Transaksi Item Movement - Odoo",
                description="Edit tanggal dan qty stock picking, SVL, dan jurnal di Odoo",
                icon_path=_icon_path(branding.EDIT_STOCK_MOVEMENT_ICON),
                module_path="smartscc_tools.modules.edit_transaksi_module",
                class_name="EditTransaksiModule",
            )
        )
        registry.register(
            LazyModuleProxy(
                module_id="svl_fix_je",
                display_name="Fixing Unlink SVL - Odoo",
                description="Validasi dan buat Journal Entry dari orphan SVL berdasarkan SVL ID",
                icon_path=_icon_path(branding.FIXING_UNLINK_SVL_ICON),
                module_path="smartscc_tools.modules.svl_fix_je_module",
                class_name="SvlFixJeModule",
            )
        )
        registry.register(
            LazyModuleProxy(
                module_id="update_std_cost",
                display_name="Update Standard Cost Item - Odoo",
                description="Update standard cost dari workbook Preparation Cost ke Odoo",
                icon_path=_icon_path(branding.UPDATE_STANDARD_COST_ICON),
                module_path="smartscc_tools.modules.update_std_cost_module",
                class_name="UpdateStdCostModule",
            )
        )
        return registry
