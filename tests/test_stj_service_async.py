import asyncio
import logging
import unittest
from typing import Any, Dict, List

import smartscc_tools.features.item_journal.services.stj as stj_module

from smartscc_tools.features.item_journal.config import RuntimeSettings
from smartscc_tools.features.item_journal.services.stj import StjServiceAsync
from smartscc_tools.features.item_journal.workbook import ItemJournalRow


def _build_rows() -> list[ItemJournalRow]:
    return [
        ItemJournalRow(
            row_number=2,
            date_done_raw="2026-03-01",
            company_id=796,
            company_name="PT Test",
            op_type="Internal Transfer",
            src_loc="WH/Stock",
            dest_loc="WH/Output",
            prod_id_text="1001",
            prod_key="",
            qty=1.0,
            uom="Units",
            result="",
            stj="",
            error="",
        ),
        ItemJournalRow(
            row_number=3,
            date_done_raw="2026-03-01",
            company_id=796,
            company_name="PT Test",
            op_type="Internal Transfer",
            src_loc="WH/Stock",
            dest_loc="WH/Output",
            prod_id_text="1002",
            prod_key="",
            qty=2.0,
            uom="Units",
            result="",
            stj="",
            error="",
        ),
    ]


class StjServiceAsyncPollingTest(unittest.TestCase):
    def test_polling_success_on_second_attempt(self) -> None:
        settings = RuntimeSettings.from_preset("safe-fast")
        settings.stj_poll_retry_count = 3
        settings.stj_poll_retry_delay_ms = 0
        service = StjServiceAsync(rpc=object(), logger=logging.getLogger("test-stj"), settings=settings)  # type: ignore[arg-type]
        rows = _build_rows()
        calls = {"count": 0}

        async def _fake_once(pick_id: int, company_id: int, target_rows: list[ItemJournalRow]):
            _ = (pick_id, company_id)
            calls["count"] += 1
            if calls["count"] == 1:
                for row in target_rows:
                    row.stj = ""
                return "STJ summary (picking=1): rows_stj_empty=2", 2, 2
            for row in target_rows:
                row.stj = "STJ/TEST/001"
            return "", 0, 2

        service._fill_stj_for_group_once = _fake_once  # type: ignore[method-assign]
        warning = asyncio.run(service.fill_stj_for_group(pick_id=1, company_id=796, target_rows=rows))

        self.assertEqual(calls["count"], 2)
        self.assertEqual(warning, "")
        self.assertTrue(all(row.stj == "STJ/TEST/001" for row in rows))

    def test_polling_exhausted_returns_attempt_warning(self) -> None:
        settings = RuntimeSettings.from_preset("safe-fast")
        settings.stj_poll_retry_count = 3
        settings.stj_poll_retry_delay_ms = 0
        service = StjServiceAsync(rpc=object(), logger=logging.getLogger("test-stj"), settings=settings)  # type: ignore[arg-type]
        rows = _build_rows()
        calls = {"count": 0}

        async def _fake_once(pick_id: int, company_id: int, target_rows: list[ItemJournalRow]):
            _ = (pick_id, company_id, target_rows)
            calls["count"] += 1
            return "STJ summary (picking=1): rows_stj_empty=2", 2, 2

        service._fill_stj_for_group_once = _fake_once  # type: ignore[method-assign]
        warning = asyncio.run(service.fill_stj_for_group(pick_id=1, company_id=796, target_rows=rows))

        self.assertEqual(calls["count"], 3)
        self.assertIn("STJ polling habis (attempt=3", warning)
        self.assertIn("rows_stj_empty=2", warning)

    def test_polling_hybrid_backoff_schedule_respects_cap(self) -> None:
        settings = RuntimeSettings.from_preset("safe-fast")
        settings.stj_poll_retry_count = 5
        settings.stj_poll_retry_delay_ms = 1000
        settings.stj_poll_fast_attempts = 2
        settings.stj_poll_max_delay_ms = 3000
        service = StjServiceAsync(rpc=object(), logger=logging.getLogger("test-stj"), settings=settings)  # type: ignore[arg-type]
        rows = _build_rows()

        async def _fake_once(pick_id: int, company_id: int, target_rows: list[ItemJournalRow]):
            _ = (pick_id, company_id, target_rows)
            return "STJ summary (picking=1): rows_stj_empty=2", 2, 2

        sleep_calls: list[float] = []
        original_sleep = stj_module.asyncio.sleep

        async def _fake_sleep(delay: float) -> None:
            sleep_calls.append(delay)

        service._fill_stj_for_group_once = _fake_once  # type: ignore[method-assign]
        stj_module.asyncio.sleep = _fake_sleep  # type: ignore[assignment]
        try:
            warning = asyncio.run(service.fill_stj_for_group(pick_id=1, company_id=796, target_rows=rows))
        finally:
            stj_module.asyncio.sleep = original_sleep  # type: ignore[assignment]

        self.assertIn("STJ polling habis (attempt=5", warning)
        self.assertEqual(sleep_calls, [1.0, 2.0, 3.0, 3.0])


def _make_row(row_number: int, product_id: int, qty: float = 1.0) -> ItemJournalRow:
    return ItemJournalRow(
        row_number=row_number,
        date_done_raw="2026-03-01",
        company_id=796,
        company_name="PT Test",
        op_type="Internal Transfer",
        src_loc="WH/Stock",
        dest_loc="WH/Output",
        prod_id_text=str(product_id),
        prod_key="",
        qty=qty,
        uom="Units",
        result="",
        stj="",
        error="",
    )


class _ScopeRpcStub:
    def __init__(
        self,
        root_origin: str,
        root_name: str,
        candidate_pickings: List[Dict[str, Any]],
    ) -> None:
        self.root_origin = root_origin
        self.root_name = root_name
        self.candidate_pickings = candidate_pickings

    async def read(self, model: str, ids: List[int], fields=None, context=None, stage: str = ""):  # noqa: ANN001, ANN003
        _ = (fields, context, stage)
        if model != "stock.picking":
            return []
        pick_id = int(ids[0]) if ids else 0
        return [
            {
                "id": pick_id,
                "name": self.root_name,
                "origin": self.root_origin,
                "state": "done",
                "company_id": [796, "PT Test"],
            }
        ]

    async def search_read(  # noqa: ANN001
        self,
        model: str,
        domain: List[Any],
        fields=None,
        order: str | None = None,
        limit: int | None = None,
        context=None,
        stage: str = "",
    ) -> List[Dict[str, Any]]:
        _ = (domain, fields, order, limit, context, stage)
        if model != "stock.picking":
            return []
        return self.candidate_pickings


class StjServiceAsyncRemapTest(unittest.TestCase):
    def _build_service(self) -> StjServiceAsync:
        settings = RuntimeSettings.from_preset("safe-fast")
        settings.stj_remap_mode = "conservative"
        settings.stj_row_split_policy = "fail"
        settings.stj_result_picking_mode = "actual"
        return StjServiceAsync(rpc=object(), logger=logging.getLogger("test-stj-remap"), settings=settings)  # type: ignore[arg-type]

    def test_conservative_remap_merged_move_success(self) -> None:
        service = self._build_service()
        rows = [_make_row(2, 1001, 1.0), _make_row(3, 1001, 1.0)]

        async def _scope(**kwargs):
            _ = kwargs
            return [100], {100: "INT/100"}, "origin_scope:technical"

        async def _moves(**kwargs):
            _ = kwargs
            return [
                {
                    "id": 500,
                    "picking_id": 100,
                    "product_id": 1001,
                    "uom_id": 1,
                    "src_loc_id": 10,
                    "dest_loc_id": 20,
                    "qty": 2.0,
                }
            ]

        async def _stj(**kwargs):
            _ = kwargs
            return {500: "STJ/500"}

        service._resolve_scope_pickings = _scope  # type: ignore[method-assign]
        service._fetch_scope_moves = _moves  # type: ignore[method-assign]
        service._collect_stj_by_move = _stj  # type: ignore[method-assign]

        row_specs = {
            2: {"row_number": 2, "product_id": 1001, "uom_id": 1, "src_loc_id": 10, "dest_loc_id": 20, "qty": 1.0},
            3: {"row_number": 3, "product_id": 1001, "uom_id": 1, "src_loc_id": 10, "dest_loc_id": 20, "qty": 1.0},
        }
        warning, missing_count, assign_count, meta = asyncio.run(
            service._fill_stj_for_group_once(
                pick_id=100,
                company_id=796,
                target_rows=rows,
                row_specs=row_specs,
                origin_scope_key="IJ:test",
            )
        )

        self.assertEqual(warning, "")
        self.assertEqual(missing_count, 0)
        self.assertEqual(assign_count, 2)
        self.assertTrue(meta.get("remap_applied"))
        self.assertFalse(meta.get("remap_failed"))
        self.assertEqual([row.stj for row in rows], ["STJ/500", "STJ/500"])
        self.assertEqual([row.result for row in rows], ["INT/100", "INT/100"])

    def test_journal_source_prioritizes_account_move_ids_then_fallback(self) -> None:
        service = self._build_service()
        rows = [_make_row(2, 1001, 1.0)]

        async def _scope(**kwargs):
            _ = kwargs
            return [100], {100: "INT/100"}, "origin_scope:technical"

        async def _moves(**kwargs):
            _ = kwargs
            return [
                {
                    "id": 600,
                    "picking_id": 100,
                    "product_id": 1001,
                    "uom_id": 1,
                    "src_loc_id": 10,
                    "dest_loc_id": 20,
                    "qty": 1.0,
                }
            ]

        async def _stj_by_pick(**kwargs):
            _ = kwargs
            return {100: "STJ/ACC/001"}

        async def _stj_by_move(**kwargs):
            _ = kwargs
            return {600: "STJ/MOVE/001"}

        service._resolve_scope_pickings = _scope  # type: ignore[method-assign]
        service._fetch_scope_moves = _moves  # type: ignore[method-assign]
        service._collect_stj_by_picking = _stj_by_pick  # type: ignore[method-assign]
        service._collect_stj_by_move = _stj_by_move  # type: ignore[method-assign]

        row_specs = {
            2: {"row_number": 2, "product_id": 1001, "uom_id": 1, "src_loc_id": 10, "dest_loc_id": 20, "qty": 1.0}
        }
        warning, missing_count, assign_count, meta = asyncio.run(
            service._fill_stj_for_group_once(
                pick_id=100,
                company_id=796,
                target_rows=rows,
                row_specs=row_specs,
                origin_scope_key="IJ:test",
            )
        )

        self.assertEqual(warning, "")
        self.assertEqual(missing_count, 0)
        self.assertEqual(assign_count, 1)
        self.assertEqual(rows[0].stj, "STJ/ACC/001;STJ/MOVE/001")
        self.assertEqual(meta.get("journal_source"), "account_move_ids+move_mapping_fallback")

    def test_journal_source_uses_move_account_move_ids_before_fallback(self) -> None:
        service = self._build_service()
        rows = [_make_row(2, 1001, 1.0)]

        async def _scope(**kwargs):
            _ = kwargs
            return [100], {100: "INT/100"}, "origin_scope:technical"

        async def _moves(**kwargs):
            _ = kwargs
            return [
                {
                    "id": 600,
                    "picking_id": 100,
                    "product_id": 1001,
                    "uom_id": 1,
                    "src_loc_id": 10,
                    "dest_loc_id": 20,
                    "qty": 1.0,
                }
            ]

        async def _stj_by_pick(**kwargs):
            _ = kwargs
            return {}

        async def _stj_by_move_relation(**kwargs):
            _ = kwargs
            return {600: "STJ/MOVE-ACC/001"}

        async def _stj_by_move(**kwargs):
            _ = kwargs
            return {600: "STJ/MOVE/001"}

        service._resolve_scope_pickings = _scope  # type: ignore[method-assign]
        service._fetch_scope_moves = _moves  # type: ignore[method-assign]
        service._collect_stj_by_picking = _stj_by_pick  # type: ignore[method-assign]
        service._collect_stj_by_move_account_move_ids = _stj_by_move_relation  # type: ignore[method-assign]
        service._collect_stj_by_move = _stj_by_move  # type: ignore[method-assign]

        row_specs = {
            2: {"row_number": 2, "product_id": 1001, "uom_id": 1, "src_loc_id": 10, "dest_loc_id": 20, "qty": 1.0}
        }
        warning, missing_count, assign_count, meta = asyncio.run(
            service._fill_stj_for_group_once(
                pick_id=100,
                company_id=796,
                target_rows=rows,
                row_specs=row_specs,
                origin_scope_key="IJ:test",
            )
        )

        self.assertEqual(warning, "")
        self.assertEqual(missing_count, 0)
        self.assertEqual(assign_count, 1)
        self.assertEqual(rows[0].stj, "STJ/MOVE-ACC/001;STJ/MOVE/001")
        self.assertEqual(meta.get("journal_source"), "move_account_move_ids+move_mapping_fallback")

    def test_conservative_remap_qty_mismatch_marks_error(self) -> None:
        service = self._build_service()
        rows = [_make_row(2, 1001, 1.0), _make_row(3, 1001, 2.0)]

        async def _scope(**kwargs):
            _ = kwargs
            return [100], {100: "INT/100"}, "origin_scope:technical"

        async def _moves(**kwargs):
            _ = kwargs
            return [
                {
                    "id": 501,
                    "picking_id": 100,
                    "product_id": 1001,
                    "uom_id": 1,
                    "src_loc_id": 10,
                    "dest_loc_id": 20,
                    "qty": 2.0,
                }
            ]

        async def _stj(**kwargs):
            _ = kwargs
            return {501: "STJ/501"}

        service._resolve_scope_pickings = _scope  # type: ignore[method-assign]
        service._fetch_scope_moves = _moves  # type: ignore[method-assign]
        service._collect_stj_by_move = _stj  # type: ignore[method-assign]

        row_specs = {
            2: {"row_number": 2, "product_id": 1001, "uom_id": 1, "src_loc_id": 10, "dest_loc_id": 20, "qty": 1.0},
            3: {"row_number": 3, "product_id": 1001, "uom_id": 1, "src_loc_id": 10, "dest_loc_id": 20, "qty": 2.0},
        }
        warning, missing_count, assign_count, meta = asyncio.run(
            service._fill_stj_for_group_once(
                pick_id=100,
                company_id=796,
                target_rows=rows,
                row_specs=row_specs,
                origin_scope_key="IJ:test",
            )
        )

        self.assertIn("STJ remap gagal", warning)
        self.assertEqual(missing_count, 0)
        self.assertEqual(assign_count, 0)
        self.assertTrue(meta.get("remap_failed"))
        self.assertTrue(all(row.result == "[Error]" for row in rows))
        self.assertTrue(any("qty mismatch" in row.error for row in rows))

    def test_conservative_remap_fail_on_row_split(self) -> None:
        service = self._build_service()
        rows = [_make_row(2, 1001, 2.0)]

        async def _scope(**kwargs):
            _ = kwargs
            return [100, 101], {100: "INT/100", 101: "INT/101"}, "origin_scope:technical"

        async def _moves(**kwargs):
            _ = kwargs
            return [
                {
                    "id": 510,
                    "picking_id": 100,
                    "product_id": 1001,
                    "uom_id": 1,
                    "src_loc_id": 10,
                    "dest_loc_id": 20,
                    "qty": 1.0,
                },
                {
                    "id": 511,
                    "picking_id": 101,
                    "product_id": 1001,
                    "uom_id": 1,
                    "src_loc_id": 10,
                    "dest_loc_id": 20,
                    "qty": 1.0,
                },
            ]

        async def _stj(**kwargs):
            _ = kwargs
            return {510: "STJ/510", 511: "STJ/511"}

        service._resolve_scope_pickings = _scope  # type: ignore[method-assign]
        service._fetch_scope_moves = _moves  # type: ignore[method-assign]
        service._collect_stj_by_move = _stj  # type: ignore[method-assign]

        row_specs = {
            2: {"row_number": 2, "product_id": 1001, "uom_id": 1, "src_loc_id": 10, "dest_loc_id": 20, "qty": 2.0}
        }
        warning, _missing_count, _assign_count, meta = asyncio.run(
            service._fill_stj_for_group_once(
                pick_id=100,
                company_id=796,
                target_rows=rows,
                row_specs=row_specs,
                origin_scope_key="IJ:test",
            )
        )

        self.assertIn("row split dibutuhkan", warning)
        self.assertTrue(meta.get("remap_failed"))
        self.assertEqual(rows[0].result, "[Error]")

    def test_actual_picking_writeback_uses_assigned_move_picking(self) -> None:
        service = self._build_service()
        rows = [_make_row(2, 1001, 1.0), _make_row(3, 1001, 1.0)]

        async def _scope(**kwargs):
            _ = kwargs
            return [100, 101], {100: "INT/100", 101: "INT/101"}, "origin_scope:technical"

        async def _moves(**kwargs):
            _ = kwargs
            return [
                {
                    "id": 520,
                    "picking_id": 100,
                    "product_id": 1001,
                    "uom_id": 1,
                    "src_loc_id": 10,
                    "dest_loc_id": 20,
                    "qty": 1.0,
                },
                {
                    "id": 521,
                    "picking_id": 101,
                    "product_id": 1001,
                    "uom_id": 1,
                    "src_loc_id": 10,
                    "dest_loc_id": 20,
                    "qty": 1.0,
                },
            ]

        async def _stj(**kwargs):
            _ = kwargs
            return {520: "STJ/520", 521: "STJ/521"}

        service._resolve_scope_pickings = _scope  # type: ignore[method-assign]
        service._fetch_scope_moves = _moves  # type: ignore[method-assign]
        service._collect_stj_by_move = _stj  # type: ignore[method-assign]

        row_specs = {
            2: {"row_number": 2, "product_id": 1001, "uom_id": 1, "src_loc_id": 10, "dest_loc_id": 20, "qty": 1.0},
            3: {"row_number": 3, "product_id": 1001, "uom_id": 1, "src_loc_id": 10, "dest_loc_id": 20, "qty": 1.0},
        }
        warning, missing_count, assign_count, _meta = asyncio.run(
            service._fill_stj_for_group_once(
                pick_id=100,
                company_id=796,
                target_rows=rows,
                row_specs=row_specs,
                origin_scope_key="IJ:test",
            )
        )

        self.assertEqual(warning, "")
        self.assertEqual(missing_count, 0)
        self.assertEqual(assign_count, 2)
        self.assertEqual(rows[0].result, "INT/100")
        self.assertEqual(rows[1].result, "INT/101")

    def test_extreme_150_rows_to_single_move_success(self) -> None:
        service = self._build_service()
        rows = [_make_row(row_number=idx + 2, product_id=1001, qty=1.0) for idx in range(150)]

        async def _scope(**kwargs):
            _ = kwargs
            return [100], {100: "INT/100"}, "origin_scope:technical"

        async def _moves(**kwargs):
            _ = kwargs
            return [
                {
                    "id": 530,
                    "picking_id": 100,
                    "product_id": 1001,
                    "uom_id": 1,
                    "src_loc_id": 10,
                    "dest_loc_id": 20,
                    "qty": 150.0,
                }
            ]

        async def _stj(**kwargs):
            _ = kwargs
            return {530: "STJ/530"}

        service._resolve_scope_pickings = _scope  # type: ignore[method-assign]
        service._fetch_scope_moves = _moves  # type: ignore[method-assign]
        service._collect_stj_by_move = _stj  # type: ignore[method-assign]

        row_specs = {
            row.row_number: {
                "row_number": row.row_number,
                "product_id": 1001,
                "uom_id": 1,
                "src_loc_id": 10,
                "dest_loc_id": 20,
                "qty": 1.0,
            }
            for row in rows
        }
        warning, missing_count, assign_count, meta = asyncio.run(
            service._fill_stj_for_group_once(
                pick_id=100,
                company_id=796,
                target_rows=rows,
                row_specs=row_specs,
                origin_scope_key="IJ:test",
            )
        )

        self.assertEqual(warning, "")
        self.assertEqual(missing_count, 0)
        self.assertEqual(assign_count, 150)
        self.assertTrue(meta.get("remap_applied"))
        self.assertTrue(all(row.stj == "STJ/530" for row in rows))

    def test_two_phase_root_done_success_stops_before_origin_phase(self) -> None:
        service = self._build_service()
        rows = [_make_row(2, 1001, 1.14)]
        calls = {"fetch": 0}

        async def _scope(**kwargs):
            include_origin_scope = bool(kwargs.get("include_origin_scope"))
            if include_origin_scope:
                return [100, 101], {100: "INT/100", 101: "INT/101"}, "origin_scope:technical"
            return [100], {100: "INT/100"}, "root_only"

        async def _moves(**kwargs):
            calls["fetch"] += 1
            scope_pick_ids = kwargs.get("scope_pick_ids", [])
            if scope_pick_ids == [100]:
                return [
                    {
                        "id": 540,
                        "picking_id": 100,
                        "product_id": 1001,
                        "uom_id": 1,
                        "src_loc_id": 10,
                        "dest_loc_id": 20,
                        "qty": 1.14,
                    }
                ]
            return [
                {
                    "id": 541,
                    "picking_id": 100,
                    "product_id": 1001,
                    "uom_id": 1,
                    "src_loc_id": 10,
                    "dest_loc_id": 20,
                    "qty": 1.14,
                },
                {
                    "id": 542,
                    "picking_id": 101,
                    "product_id": 1001,
                    "uom_id": 1,
                    "src_loc_id": 10,
                    "dest_loc_id": 20,
                    "qty": 1.14,
                },
            ]

        async def _stj(**kwargs):
            _ = kwargs
            return {540: "STJ/540", 541: "STJ/541", 542: "STJ/542"}

        service._resolve_scope_pickings = _scope  # type: ignore[method-assign]
        service._fetch_scope_moves = _moves  # type: ignore[method-assign]
        service._collect_stj_by_move = _stj  # type: ignore[method-assign]

        row_specs = {
            2: {"row_number": 2, "product_id": 1001, "uom_id": 1, "src_loc_id": 10, "dest_loc_id": 20, "qty": 1.14}
        }
        warning, missing_count, assign_count, meta = asyncio.run(
            service._fill_stj_for_group_once(
                pick_id=100,
                company_id=796,
                target_rows=rows,
                row_specs=row_specs,
                origin_scope_key="IJ:test",
            )
        )

        self.assertEqual(warning, "")
        self.assertEqual(missing_count, 0)
        self.assertEqual(assign_count, 1)
        self.assertEqual(calls["fetch"], 1)
        self.assertEqual(meta.get("scope_phase"), "root_done")
        self.assertEqual(rows[0].result, "INT/100")

    def test_two_phase_origin_done_success_after_root_mismatch(self) -> None:
        service = self._build_service()
        rows = [_make_row(2, 1001, 1.14)]
        calls = {"fetch": 0}

        async def _scope(**kwargs):
            include_origin_scope = bool(kwargs.get("include_origin_scope"))
            if include_origin_scope:
                return [100, 101], {100: "INT/100", 101: "INT/101"}, "origin_scope:technical"
            return [100], {100: "INT/100"}, "root_only"

        async def _moves(**kwargs):
            calls["fetch"] += 1
            scope_pick_ids = kwargs.get("scope_pick_ids", [])
            if scope_pick_ids == [100]:
                return [
                    {
                        "id": 550,
                        "picking_id": 100,
                        "product_id": 1001,
                        "uom_id": 1,
                        "src_loc_id": 10,
                        "dest_loc_id": 20,
                        "qty": 2.28,
                    }
                ]
            return [
                {
                    "id": 551,
                    "picking_id": 101,
                    "product_id": 1001,
                    "uom_id": 1,
                    "src_loc_id": 10,
                    "dest_loc_id": 20,
                    "qty": 1.14,
                }
            ]

        async def _stj(**kwargs):
            _ = kwargs
            return {550: "STJ/550", 551: "STJ/551"}

        service._resolve_scope_pickings = _scope  # type: ignore[method-assign]
        service._fetch_scope_moves = _moves  # type: ignore[method-assign]
        service._collect_stj_by_move = _stj  # type: ignore[method-assign]

        row_specs = {
            2: {"row_number": 2, "product_id": 1001, "uom_id": 1, "src_loc_id": 10, "dest_loc_id": 20, "qty": 1.14}
        }
        warning, missing_count, assign_count, meta = asyncio.run(
            service._fill_stj_for_group_once(
                pick_id=100,
                company_id=796,
                target_rows=rows,
                row_specs=row_specs,
                origin_scope_key="IJ:test",
            )
        )

        self.assertEqual(warning, "")
        self.assertEqual(missing_count, 0)
        self.assertEqual(assign_count, 1)
        self.assertEqual(calls["fetch"], 2)
        self.assertEqual(meta.get("scope_phase"), "origin_done")
        self.assertIn("scope_phase=origin_done", str(meta.get("remap_message")))
        self.assertEqual(rows[0].result, "INT/101")
        self.assertNotEqual(rows[0].result, "[Error]")

    def test_two_phase_root_fallback_done_fails_when_all_scopes_mismatch(self) -> None:
        service = self._build_service()
        rows = [_make_row(2, 1001, 1.14)]
        calls = {"fetch": 0}

        async def _scope(**kwargs):
            include_origin_scope = bool(kwargs.get("include_origin_scope"))
            if include_origin_scope:
                return [100, 101], {100: "INT/100", 101: "INT/101"}, "origin_scope:technical"
            return [100], {100: "INT/100"}, "root_only"

        async def _moves(**kwargs):
            _ = kwargs
            calls["fetch"] += 1
            return [
                {
                    "id": 560 + calls["fetch"],
                    "picking_id": 100,
                    "product_id": 1001,
                    "uom_id": 1,
                    "src_loc_id": 10,
                    "dest_loc_id": 20,
                    "qty": 2.28,
                }
            ]

        async def _stj(**kwargs):
            _ = kwargs
            return {}

        service._resolve_scope_pickings = _scope  # type: ignore[method-assign]
        service._fetch_scope_moves = _moves  # type: ignore[method-assign]
        service._collect_stj_by_move = _stj  # type: ignore[method-assign]

        row_specs = {
            2: {"row_number": 2, "product_id": 1001, "uom_id": 1, "src_loc_id": 10, "dest_loc_id": 20, "qty": 1.14}
        }
        warning, missing_count, assign_count, meta = asyncio.run(
            service._fill_stj_for_group_once(
                pick_id=100,
                company_id=796,
                target_rows=rows,
                row_specs=row_specs,
                origin_scope_key="IJ:test",
            )
        )

        self.assertIn("scope_phase=root_fallback_done", warning)
        self.assertEqual(missing_count, 0)
        self.assertEqual(assign_count, 0)
        self.assertEqual(calls["fetch"], 3)
        self.assertTrue(meta.get("remap_failed"))
        self.assertEqual(meta.get("scope_phase"), "root_fallback_done")
        self.assertEqual(rows[0].result, "[Error]")

    def test_origin_scope_uses_human_suffix_token(self) -> None:
        settings = RuntimeSettings.from_preset("safe-fast")
        settings.create_origin_mode = "human_suffix"
        settings.create_origin_human_ref = "OPNAME-A"
        service = StjServiceAsync(
            rpc=_ScopeRpcStub(
                root_origin="OPNAME-A [ID:IJ:run-1:1:0:root:abcd1234]",
                root_name="INT/ROOT",
                candidate_pickings=[
                    {
                        "id": 11,
                        "name": "INT/CHILD",
                        "state": "done",
                        "origin": "OPNAME-A [ID:IJ:run-1:1:0:root:abcd1234]",
                        "company_id": [796, "PT Test"],
                    }
                ],
            ),  # type: ignore[arg-type]
            logger=logging.getLogger("test-stj-scope"),
            settings=settings,
        )

        pick_ids, pick_names, source = asyncio.run(
            service._resolve_scope_pickings(
                root_pick_id=10,
                company_id=796,
                origin_scope_key="IJ:run-1:1:0:root:abcd1234",
            )
        )

        self.assertEqual(pick_ids, [10, 11])
        self.assertEqual(pick_names[10], "INT/ROOT")
        self.assertEqual(pick_names[11], "INT/CHILD")
        self.assertIn("origin_scope", source)


if __name__ == "__main__":
    unittest.main()
