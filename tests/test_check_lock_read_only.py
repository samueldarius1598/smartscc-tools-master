import argparse
import asyncio
import logging
import unittest
from dataclasses import dataclass
from datetime import date
from types import SimpleNamespace
from unittest import mock

import smartscc_tools.features.item_journal.entrypoints.cli as cli_module

from smartscc_tools.features.item_journal.entrypoints.cli import run_check_lock
from smartscc_tools.features.item_journal.services.date_ops import LockDateServiceAsync
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
        )
    ]


@dataclass
class _RepoStub:
    rows: list[ItemJournalRow]
    write_lock_date_calls: int = 0
    write_status_calls: int = 0

    def read_rows(self) -> list[ItemJournalRow]:
        return self.rows

    def write_lock_date(self, _value: date | None) -> None:
        self.write_lock_date_calls += 1

    def write_status(self, _text: str) -> None:
        self.write_status_calls += 1


class _DummyRpc:
    pass


class _RepoForCli:
    def __init__(self, _workbook_path: str) -> None:
        self.rows = _build_rows()
        self.save_called = False

    def read_rows(self) -> list[ItemJournalRow]:
        return self.rows

    def save(self, *args, **kwargs):  # noqa: ANN002, ANN003
        self.save_called = True
        raise AssertionError("run_check_lock tidak boleh memanggil save()")


class _LockServiceAsyncForCli:
    def __init__(self, rpc, logger) -> None:  # noqa: ANN001
        _ = (rpc, logger)

    async def check_close_acc_date(self, repo) -> date | None:  # noqa: ANN001
        _ = repo
        return date(2026, 3, 1)


class _AsyncRpcCtx:
    def __init__(self, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
        _ = (args, kwargs)

    async def __aenter__(self):
        return object()

    async def __aexit__(self, exc_type, exc, tb):  # noqa: ANN001
        _ = (exc_type, exc, tb)
        return False


class CheckLockReadOnlyTest(unittest.TestCase):
    def test_async_lock_service_check_close_read_only(self) -> None:
        repo = _RepoStub(rows=_build_rows())
        service = LockDateServiceAsync(rpc=_DummyRpc(), logger=logging.getLogger("test-check-lock-async"))  # type: ignore[arg-type]

        async def _get_lock(_company_id: int):
            return date(2026, 3, 1), ""

        service.get_close_acc_date_by_company_id = _get_lock  # type: ignore[method-assign]
        lock_date = asyncio.run(service.check_close_acc_date(repo))

        self.assertEqual(lock_date, date(2026, 3, 1))
        self.assertEqual(repo.write_lock_date_calls, 0)
        self.assertEqual(repo.write_status_calls, 0)

    def test_cli_run_check_lock_async_does_not_save(self) -> None:
        args = argparse.Namespace(
            preset="safe-fast",
            set=[],
            log_file="",
            verbose=False,
            workbook="dummy.xlsm",
            db_override="",
            max_concurrency=8,
            save_mode="in-place",
        )

        with (
            mock.patch.object(cli_module, "ItemJournalWorkbookRepo", _RepoForCli),
            mock.patch.object(cli_module, "fetch_odoo_config", return_value=SimpleNamespace(database="test_db")),
            mock.patch.object(cli_module, "AsyncOdooJsonRpcClient", _AsyncRpcCtx),
            mock.patch.object(cli_module, "LockDateServiceAsync", _LockServiceAsyncForCli),
        ):
            rc = run_check_lock(args)

        self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
