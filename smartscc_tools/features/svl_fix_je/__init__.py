"""SVL Fix JE business package.

Keep package import light. Heavy services and models are resolved lazily via
``__getattr__`` so module adapters can import config without paying full
startup cost.
"""

from __future__ import annotations

from importlib import import_module

__all__ = [
    "SvlDashboardCompany",
    "SvlDashboardItem",
    "SvlDashboardProgressSnapshot",
    "SvlDashboardRequest",
    "SvlDashboardSnapshot",
    "SvlDashboardServiceAsync",
    "SvlFixJeExcelRow",
    "SvlFixJeProgressSnapshot",
    "SvlFixJeRowResult",
    "SvlFixJeRunRequest",
    "SvlFixJeRunSummary",
    "SvlFixJeServiceAsync",
    "SvlFixJeSettings",
    "SvlFixJeValidatedRow",
]

_ATTRIBUTE_MODULES = {
    "SvlFixJeSettings": "smartscc_tools.features.svl_fix_je.config",
    "SvlDashboardCompany": "smartscc_tools.features.svl_fix_je.models",
    "SvlDashboardItem": "smartscc_tools.features.svl_fix_je.models",
    "SvlDashboardProgressSnapshot": "smartscc_tools.features.svl_fix_je.models",
    "SvlDashboardRequest": "smartscc_tools.features.svl_fix_je.models",
    "SvlDashboardSnapshot": "smartscc_tools.features.svl_fix_je.models",
    "SvlFixJeExcelRow": "smartscc_tools.features.svl_fix_je.models",
    "SvlFixJeProgressSnapshot": "smartscc_tools.features.svl_fix_je.models",
    "SvlFixJeRowResult": "smartscc_tools.features.svl_fix_je.models",
    "SvlFixJeRunRequest": "smartscc_tools.features.svl_fix_je.models",
    "SvlFixJeRunSummary": "smartscc_tools.features.svl_fix_je.models",
    "SvlFixJeValidatedRow": "smartscc_tools.features.svl_fix_je.models",
    "SvlDashboardServiceAsync": "smartscc_tools.features.svl_fix_je.dashboard_service",
    "SvlFixJeServiceAsync": "smartscc_tools.features.svl_fix_je.service",
}


def __getattr__(name: str):
    module_name = _ATTRIBUTE_MODULES.get(name)
    if module_name is None:
        raise AttributeError(f"module 'smartscc_tools.features.svl_fix_je' has no attribute {name!r}")
    module = import_module(module_name)
    value = getattr(module, name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
