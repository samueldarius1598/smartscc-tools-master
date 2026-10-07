import logging
import unittest

from smartscc_tools.features.item_journal.config import RuntimeSettings
from smartscc_tools.services.odoo.gateway import (
    AsyncOdooJsonRpcClient,
    OdooConfig,
    OdooRpcError,
    OdooTransport,
    RpcCallContext,
)


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict | None = None, text: str = "") -> None:
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.text = text or str(self._payload)

    def json(self):
        return self._payload


class _FakeAsyncClient:
    def __init__(self, responses: list[_FakeResponse]) -> None:
        self.responses = list(responses)
        self.posts: list[dict] = []
        self.closed = False

    async def post(self, url: str, json: dict):  # noqa: A002
        self.posts.append({"url": url, "json": json})
        if not self.responses:
            raise AssertionError("No more fake responses queued.")
        return self.responses.pop(0)

    async def aclose(self) -> None:
        self.closed = True


class GatewayTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.logger = logging.getLogger("test.gateway")
        self.config = OdooConfig(
            base_url="https://odoo.example.com",
            database="demo",
            user_email="user@example.com",
            uid=0,
            api_key="secret",
        )
        self.settings = RuntimeSettings.from_preset("safe-fast")
        self.settings.transport_retry_backoff_seconds = 0

    async def test_ensure_login_is_cached(self) -> None:
        client = _FakeAsyncClient([_FakeResponse(200, {"result": 77})])
        rpc = AsyncOdooJsonRpcClient(
            config=self.config,
            settings=self.settings,
            logger=self.logger,
            client=client,
        )

        uid1 = await rpc.ensure_login()
        uid2 = await rpc.ensure_login()

        self.assertEqual(uid1, 77)
        self.assertEqual(uid2, 77)
        self.assertEqual(len(client.posts), 1)
        self.assertEqual(self.config.uid, 77)

    async def test_transport_retries_and_emits_telemetry(self) -> None:
        telemetry = []
        client = _FakeAsyncClient(
            [
                _FakeResponse(503, {"error": "temporary"}, text="unavailable"),
                _FakeResponse(200, {"result": {"ok": True}}),
            ]
        )
        transport = OdooTransport(
            config=self.config,
            settings=self.settings,
            logger=self.logger,
            client=client,
            rpc_telemetry_callback=telemetry.append,
        )
        self.settings.transport_max_retries = 2

        body = await transport.request(
            payload={"jsonrpc": "2.0", "method": "call", "params": {}, "id": 1},
            rpc_label="demo.call",
            use_write_lock=False,
            use_read_sem=False,
            call_context=RpcCallContext(stage="TEST", model="x.demo", method="call", excel_row=9),
        )

        self.assertEqual(body["result"], {"ok": True})
        self.assertEqual(len(client.posts), 2)
        self.assertEqual(len(telemetry), 2)
        self.assertEqual(telemetry[-1].status, "ok")
        self.assertEqual(telemetry[-1].stage, "TEST")
        self.assertEqual(telemetry[-1].excel_row, 9)

    async def test_transport_uses_runtime_read_concurrency_by_default(self) -> None:
        self.settings.max_read_concurrency = 13
        transport = OdooTransport(
            config=self.config,
            settings=self.settings,
            logger=self.logger,
            client=_FakeAsyncClient([]),
        )

        self.assertEqual(transport.max_read_concurrency, 13)

    async def test_transport_explicit_max_concurrency_overrides_runtime_setting(self) -> None:
        self.settings.max_read_concurrency = 13
        transport = OdooTransport(
            config=self.config,
            settings=self.settings,
            logger=self.logger,
            max_concurrency=5,
            client=_FakeAsyncClient([]),
        )

        self.assertEqual(transport.max_read_concurrency, 5)

    async def test_execute_kw_raises_described_odoo_error(self) -> None:
        client = _FakeAsyncClient(
            [
                _FakeResponse(200, {"result": 77}),
                _FakeResponse(
                    200,
                    {
                        "error": {
                            "data": {
                                "name": "odoo.exceptions.UserError",
                                "message": "Invalid move",
                            }
                        }
                    },
                ),
            ]
        )
        rpc = AsyncOdooJsonRpcClient(
            config=self.config,
            settings=self.settings,
            logger=self.logger,
            client=client,
        )

        with self.assertRaises(OdooRpcError) as ctx:
            await rpc.execute_kw(model="stock.move", method="write", args=[[1], {"x": 1}], mutating=True)

        self.assertIn("Invalid move", str(ctx.exception))

    async def test_read_group_forwards_payload_and_returns_rows(self) -> None:
        client = _FakeAsyncClient(
            [
                _FakeResponse(200, {"result": 77}),
                _FakeResponse(200, {"result": [{"product_id": [11, "Demo"], "value": 125.5}]}),
            ]
        )
        rpc = AsyncOdooJsonRpcClient(
            config=self.config,
            settings=self.settings,
            logger=self.logger,
            client=client,
        )

        rows = await rpc.read_group(
            model="stock.valuation.layer",
            domain=[("company_id", "=", 1)],
            fields=["product_id", "value"],
            groupby=["product_id"],
            lazy=False,
            context={"company_id": 1},
            stage="TEST_READ_GROUP",
        )

        self.assertEqual(rows[0]["value"], 125.5)
        payload = client.posts[1]["json"]["params"]["args"]
        self.assertEqual(payload[3], "stock.valuation.layer")
        self.assertEqual(payload[4], "read_group")
        self.assertEqual(payload[5][0], [("company_id", "=", 1)])
        self.assertEqual(payload[5][1], ["product_id", "value"])
        self.assertEqual(payload[5][2], ["product_id"])
        self.assertFalse(payload[6]["lazy"])
        self.assertEqual(payload[6]["context"], {"company_id": 1})
