import sys
import types
import unittest
from unittest import mock

from smartscc_tools.core.module_registry import LazyModuleProxy, ModuleRegistry


class _DummyModule:
    def __init__(self, module_id: str) -> None:
        self._module_id = module_id

    @property
    def module_id(self) -> str:
        return self._module_id

    @property
    def display_name(self) -> str:
        return self._module_id

    @property
    def description(self) -> str:
        return self._module_id

    def create_ui(self, parent, context):
        return parent


class ModuleRegistryTest(unittest.TestCase):
    def test_create_default_registers_expected_modules_in_order(self) -> None:
        registry = ModuleRegistry.create_default()

        self.assertEqual(
            [module.module_id for module in registry.all_modules()],
            ["item_journal", "edit_transaksi", "svl_fix_je", "update_std_cost"],
        )
        self.assertEqual(registry.get("svl_fix_je").display_name, "Fixing Unlink SVL - Odoo")

    def test_create_default_does_not_import_module_classes(self) -> None:
        with mock.patch("importlib.import_module") as import_module:
            registry = ModuleRegistry.create_default()

        import_module.assert_not_called()
        self.assertEqual(registry.get("item_journal").display_name, "Internal Transfer - Odoo")

    def test_lazy_module_proxy_loads_real_module_on_first_create_ui(self) -> None:
        module_name = "tests._fake_lazy_module"
        fake_module = types.ModuleType(module_name)

        class _FakeLazyModule:
            instances = []

            def __init__(self) -> None:
                self.activated = False
                _FakeLazyModule.instances.append(self)

            @property
            def module_id(self) -> str:
                return "fake"

            @property
            def display_name(self) -> str:
                return "Fake"

            @property
            def description(self) -> str:
                return "Fake module"

            def create_ui(self, parent, context):
                self.parent = parent
                self.context = context
                return "created"

            def on_activate(self) -> None:
                self.activated = True

        fake_module.FakeLazyModule = _FakeLazyModule
        sys.modules[module_name] = fake_module
        try:
            proxy = LazyModuleProxy(
                module_id="fake",
                display_name="Fake",
                description="Fake module",
                module_path=module_name,
                class_name="FakeLazyModule",
            )

            self.assertEqual(proxy.create_ui("parent", "context"), "created")
            proxy.on_activate()

            self.assertEqual(len(_FakeLazyModule.instances), 1)
            self.assertEqual(_FakeLazyModule.instances[0].parent, "parent")
            self.assertEqual(_FakeLazyModule.instances[0].context, "context")
            self.assertTrue(_FakeLazyModule.instances[0].activated)
        finally:
            sys.modules.pop(module_name, None)

    def test_register_rejects_duplicate_module_id(self) -> None:
        registry = ModuleRegistry()
        registry.register(_DummyModule("alpha"))

        with self.assertRaises(ValueError):
            registry.register(_DummyModule("alpha"))


if __name__ == "__main__":
    unittest.main()
