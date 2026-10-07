import unittest

from smartscc_tools.services.odoo.gateway import OdooRpcError
from smartscc_tools.services.odoo.schema import snapshot_model_schema


class _MetadataDeniedRpc:
    async def search_read(self, model, domain, **kwargs):  # noqa: ANN001, ANN003
        if model == "ir.model":
            raise OdooRpcError("ir.model access denied")
        raise AssertionError(f"Unexpected search_read model: {model}")

    async def fields_get(self, model, attributes=None, context=None, stage=""):  # noqa: ANN001
        self.fields_get_call = {
            "model": model,
            "attributes": attributes,
            "context": context,
            "stage": stage,
        }
        return {
            "name": {"type": "char", "string": "Name"},
            "relative_uom_id": {"type": "many2one", "relation": "uom.uom"},
        }


class OdooSchemaTest(unittest.IsolatedAsyncioTestCase):
    async def test_snapshot_model_schema_falls_back_when_model_metadata_is_denied(self) -> None:
        rpc = _MetadataDeniedRpc()

        snapshot = await snapshot_model_schema(rpc, "uom.uom")

        self.assertEqual(snapshot.model, "uom.uom")
        self.assertEqual(snapshot.model_row["model"], "uom.uom")
        self.assertIn("metadata_access_error", snapshot.model_row)
        self.assertIn("relative_uom_id", snapshot.fields)
        self.assertEqual(rpc.fields_get_call["model"], "uom.uom")


if __name__ == "__main__":
    unittest.main()
