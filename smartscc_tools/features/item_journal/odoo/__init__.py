"""Odoo gateway compatibility surface for the Item Journal feature."""

from smartscc_tools.services.odoo.gateway import (
    AsyncOdooClient,
    AsyncOdooJsonRpcClient,
    OdooConfig,
    OdooRpcError,
    OdooTransport,
    RpcCallContext,
    build_company_context,
    describe_odoo_error,
    extract_many2one_id,
    fetch_odoo_config,
)

__all__ = [
    "AsyncOdooClient",
    "AsyncOdooJsonRpcClient",
    "OdooConfig",
    "OdooRpcError",
    "OdooTransport",
    "RpcCallContext",
    "build_company_context",
    "describe_odoo_error",
    "extract_many2one_id",
    "fetch_odoo_config",
]
