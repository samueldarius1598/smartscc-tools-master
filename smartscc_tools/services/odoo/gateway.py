"""Odoo boundary: config fetch, transport, and async CRUD client."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import logging
import time
from typing import Any, Callable, Dict, Iterable, List

import httpx
import requests

from smartscc_tools.features.item_journal.config import RuntimeSettings
from smartscc_tools.features.item_journal.observability.perf import RpcTelemetry
from smartscc_tools.features.item_journal.utils import normalize_text
from smartscc_tools.services.gas.credentials import load_gas_api_key, load_gas_setting


GAS_BASE_URL = (
    "https://script.google.com/macros/s/"
    "AKfycbwmRLmYx_A3xXmBIS8cEWdxmSqDRXG-iaOxwCV2--u91TW1jdJxY-ud25jX4zzO6Qgm/exec"
)
GAS_API_KEY = load_gas_api_key()
FALLBACK_CONFIG_SSID = load_gas_setting("SMARTSCC_GAS_FALLBACK_CONFIG_SSID", "fallback_config_ssid.txt")
CONFIG_GID = 1746209771
HTTP_RETRYABLE_STATUS = {408, 429, 500, 502, 503, 504}
GAS_RETRYABLE_HTTP_STATUS = HTTP_RETRYABLE_STATUS


@dataclass
class OdooConfig:
    base_url: str
    database: str
    user_email: str
    uid: int
    api_key: str


class OdooRpcError(RuntimeError):
    def __init__(self, message: str, status_code: int = 0, response_text: str = "") -> None:
        super().__init__(message)
        self.status_code = status_code
        self.response_text = response_text


def describe_odoo_error(error_obj: Any) -> str:
    if not isinstance(error_obj, dict):
        return str(error_obj)

    data = error_obj.get("data")
    if isinstance(data, dict):
        name = str(data.get("name") or "").strip()
        message = str(data.get("message") or data.get("debug") or "").strip()
        if name and message:
            return f"{name}: {message}"
        if message:
            return message

    message = str(error_obj.get("message") or "").strip()
    if message:
        return message
    return str(error_obj)


def build_company_context(company_id: int) -> Dict[str, Any]:
    if company_id <= 0:
        return {}
    return {
        "company_id": company_id,
        "force_company": company_id,
        "allowed_company_ids": [company_id],
    }


def extract_many2one_id(value: Any) -> int:
    if isinstance(value, (list, tuple)) and value:
        try:
            return int(value[0])
        except (TypeError, ValueError):
            return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _trim_trailing_slash(text: str) -> str:
    output = text.strip()
    while output.endswith("/"):
        output = output[:-1]
    return output


def _normalize_values(values: Iterable[Iterable[Any]]) -> List[List[Any]]:
    out: List[List[Any]] = []
    for row in values:
        if isinstance(row, list):
            out.append(row)
        else:
            out.append(list(row))
    return out


def _first_cell(values: List[List[Any]], row_idx: int) -> Any:
    if row_idx >= len(values):
        return ""
    row = values[row_idx]
    if not row:
        return ""
    return row[0]


def _strip_csv_cell(text: str) -> str:
    output = text
    if len(output) >= 2 and output.startswith('"') and output.endswith('"'):
        output = output[1:-1].replace('""', '"')
    return output.strip()


def _request_with_retry(
    session: requests.Session,
    url: str,
    settings: RuntimeSettings,
    params: Dict[str, str] | None = None,
) -> requests.Response | None:
    last_response: requests.Response | None = None
    for attempt in range(1, settings.gas_max_retries + 1):
        try:
            response = session.get(
                url,
                params=params,
                timeout=(settings.gas_timeout_connect, settings.gas_timeout_read),
                headers={"Accept": "application/json,text/plain,*/*"},
            )
            last_response = response
            if response.status_code not in GAS_RETRYABLE_HTTP_STATUS:
                return response
        except requests.RequestException:
            response = None
        if attempt < settings.gas_max_retries:
            time.sleep(settings.gas_retry_backoff_seconds * attempt)
    return last_response


def _fetch_fallback_csv(gid: int, session: requests.Session, settings: RuntimeSettings) -> List[List[str]]:
    if not FALLBACK_CONFIG_SSID:
        return []
    csv_url = (
        "https://docs.google.com/spreadsheets/d/"
        f"{FALLBACK_CONFIG_SSID}/export?format=csv&gid={gid}&range=B2:B6"
    )
    response = _request_with_retry(session, csv_url, settings)
    if response is None or response.status_code != 200 or not response.text:
        return []
    lines = [line for line in response.text.replace("\r", "").split("\n") if line != ""]
    return [[_strip_csv_cell(line)] for line in lines]


def fetch_odoo_config(
    settings: RuntimeSettings,
    gid: int = CONFIG_GID,
    session: requests.Session | None = None,
) -> OdooConfig:
    if not GAS_API_KEY:
        raise RuntimeError("API key GAS belum diatur. Lihat docs/github_handoff.md.")
    sess = session or requests.Session()
    params = {
        "key": GAS_API_KEY,
        "gid": str(gid),
        "range": "B2:B6",
        "type": "raw",
    }

    response = _request_with_retry(sess, GAS_BASE_URL, settings, params=params)
    values: List[List[Any]] | None = None
    if response is not None and response.status_code == 200 and response.text:
        try:
            root = response.json()
        except ValueError:
            root = {}
        if root.get("ok") and isinstance(root.get("values"), list):
            values = _normalize_values(root["values"])

    if not values:
        values = _fetch_fallback_csv(gid, sess, settings)

    if len(values) < 5:
        raise RuntimeError("Konfigurasi Odoo dari GAS belum lengkap (B2:B6).")

    base_url = _trim_trailing_slash(normalize_text(_first_cell(values, 0)))
    database = normalize_text(_first_cell(values, 1))
    user_email = normalize_text(_first_cell(values, 2))
    uid_text = normalize_text(_first_cell(values, 3))
    api_key = normalize_text(_first_cell(values, 4))

    uid = 0
    if uid_text:
        try:
            uid = int(float(uid_text))
        except (TypeError, ValueError):
            uid = 0

    if not base_url or not database or not user_email or not api_key:
        raise RuntimeError("Konfigurasi Odoo dari GAS tidak valid.")

    return OdooConfig(
        base_url=base_url,
        database=database,
        user_email=user_email,
        uid=uid,
        api_key=api_key,
    )


@dataclass
class RpcCallContext:
    stage: str = ""
    model: str = ""
    method: str = ""
    excel_row: int = 0


class OdooTransport:
    """Transport layer for session, login, raw request, and retry."""

    def __init__(
        self,
        config: OdooConfig,
        settings: RuntimeSettings,
        logger: logging.Logger,
        max_concurrency: int | None = None,
        client: httpx.AsyncClient | None = None,
        rpc_telemetry_callback: Callable[[RpcTelemetry], None] | None = None,
    ) -> None:
        self.config = config
        self.settings = settings
        self.logger = logger
        self.json_rpc_url = f"{config.base_url.rstrip('/')}/jsonrpc"
        self.call_context = RpcCallContext()
        timeout = httpx.Timeout(settings.http_timeout_read, connect=settings.http_timeout_connect)
        self.client = client or httpx.AsyncClient(
            timeout=timeout,
            headers={"Content-Type": "application/json"},
        )
        raw_max_read_concurrency = (
            getattr(settings, "max_read_concurrency", 8) if max_concurrency is None else max_concurrency
        )
        self.max_read_concurrency = max(1, int(raw_max_read_concurrency))
        self.read_semaphore = asyncio.Semaphore(self.max_read_concurrency)
        self.write_lock = asyncio.Lock()
        self.login_lock = asyncio.Lock()
        self.last_http_status = 0
        self.last_duration_ms = 0
        self.rpc_telemetry_callback = rpc_telemetry_callback

    @property
    def uid(self) -> int:
        return int(self.config.uid or 0)

    async def close(self) -> None:
        await self.client.aclose()

    async def ensure_login(self) -> int:
        if self.uid > 0:
            return self.uid
        async with self.login_lock:
            if self.uid > 0:
                return self.uid
            payload = {
                "jsonrpc": "2.0",
                "method": "call",
                "params": {
                    "service": "common",
                    "method": "login",
                    "args": [self.config.database, self.config.user_email, self.config.api_key],
                },
                "id": 1,
            }
            call_context = RpcCallContext(stage="AUTH_CHECK", model="common", method="login", excel_row=0)
            body = await self.request(
                payload=payload,
                rpc_label="common.login",
                use_write_lock=False,
                use_read_sem=False,
                call_context=call_context,
            )
            uid = body.get("result", 0)
            try:
                uid_int = int(uid)
            except (TypeError, ValueError):
                uid_int = 0
            if uid_int <= 0:
                raise OdooRpcError("Login Odoo gagal: UID tidak valid.")
            self.config.uid = uid_int
            self.logger.info("AUTH_CHECK sukses. uid=%s", uid_int)
            return uid_int

    async def request(
        self,
        payload: Dict[str, Any],
        rpc_label: str,
        use_write_lock: bool,
        use_read_sem: bool,
        call_context: RpcCallContext,
    ) -> Dict[str, Any]:
        if self.settings.validate_json_payload_on_each_call:
            try:
                json.dumps(payload)
            except (TypeError, ValueError) as exc:
                raise OdooRpcError(f"Payload JSON tidak valid untuk {rpc_label}: {exc}") from exc

        async def do_post() -> tuple[httpx.Response, float, float]:
            started_perf = time.perf_counter()
            started_utc = datetime.now(timezone.utc).timestamp()
            response = await self.client.post(self.json_rpc_url, json=payload)
            self.last_http_status = int(response.status_code)
            ended_perf = time.perf_counter()
            self.last_duration_ms = int((ended_perf - started_perf) * 1000)
            return response, started_utc, datetime.now(timezone.utc).timestamp()

        last_status = 0
        last_text = ""
        last_error_message = "Unknown error"

        for attempt in range(1, self.settings.transport_max_retries + 1):
            try:
                if use_write_lock:
                    async with self.write_lock:
                        response, started_ts, ended_ts = await do_post()
                elif use_read_sem:
                    async with self.read_semaphore:
                        response, started_ts, ended_ts = await do_post()
                else:
                    response, started_ts, ended_ts = await do_post()

                last_status = response.status_code
                last_text = response.text or ""

                if response.status_code in HTTP_RETRYABLE_STATUS:
                    last_error_message = f"HTTP status {response.status_code}"
                    self._emit_rpc_telemetry(
                        call_context=call_context,
                        started_ts=started_ts,
                        ended_ts=ended_ts,
                        duration_ms=self.last_duration_ms,
                        status="error",
                        attempt=attempt,
                        http_status=response.status_code,
                        error_class="HTTP_RETRYABLE",
                        error_message=last_error_message,
                    )
                    if attempt < self.settings.transport_max_retries:
                        await asyncio.sleep(self.settings.transport_retry_backoff_seconds * attempt)
                        continue

                if response.status_code != 200:
                    self._emit_rpc_telemetry(
                        call_context=call_context,
                        started_ts=started_ts,
                        ended_ts=ended_ts,
                        duration_ms=self.last_duration_ms,
                        status="error",
                        attempt=attempt,
                        http_status=response.status_code,
                        error_class="HTTP_STATUS",
                        error_message=f"HTTP status {response.status_code}",
                    )
                    raise OdooRpcError(
                        f"{rpc_label} gagal. HTTP status {response.status_code}",
                        status_code=response.status_code,
                        response_text=last_text,
                    )

                try:
                    body = response.json()
                except ValueError as exc:
                    raise OdooRpcError(
                        f"{rpc_label} gagal: response bukan JSON valid.",
                        status_code=response.status_code,
                        response_text=last_text,
                    ) from exc

                if "error" in body:
                    message = describe_odoo_error(body["error"])
                    self._emit_rpc_telemetry(
                        call_context=call_context,
                        started_ts=started_ts,
                        ended_ts=ended_ts,
                        duration_ms=self.last_duration_ms,
                        status="error",
                        attempt=attempt,
                        http_status=response.status_code,
                        error_class="ODOO_ERROR",
                        error_message=message,
                    )
                    raise OdooRpcError(
                        f"{rpc_label} Odoo error: {message}",
                        status_code=response.status_code,
                        response_text=last_text,
                    )

                self._emit_rpc_telemetry(
                    call_context=call_context,
                    started_ts=started_ts,
                    ended_ts=ended_ts,
                    duration_ms=self.last_duration_ms,
                    status="ok",
                    attempt=attempt,
                    http_status=response.status_code,
                    error_class="",
                    error_message="",
                )
                return body

            except httpx.TimeoutException as exc:
                last_error_message = f"timeout: {exc}"
                self._emit_rpc_telemetry(
                    call_context=call_context,
                    started_ts=datetime.now(timezone.utc).timestamp(),
                    ended_ts=datetime.now(timezone.utc).timestamp(),
                    duration_ms=self.last_duration_ms,
                    status="exception",
                    attempt=attempt,
                    http_status=last_status,
                    error_class=exc.__class__.__name__,
                    error_message=last_error_message,
                )
                if attempt < self.settings.transport_max_retries:
                    await asyncio.sleep(self.settings.transport_retry_backoff_seconds * attempt)
                    continue
                raise OdooRpcError(f"{rpc_label} timeout.", response_text=last_text) from exc
            except httpx.HTTPError as exc:
                last_error_message = str(exc)
                self._emit_rpc_telemetry(
                    call_context=call_context,
                    started_ts=datetime.now(timezone.utc).timestamp(),
                    ended_ts=datetime.now(timezone.utc).timestamp(),
                    duration_ms=self.last_duration_ms,
                    status="exception",
                    attempt=attempt,
                    http_status=last_status,
                    error_class=exc.__class__.__name__,
                    error_message=last_error_message,
                )
                if attempt < self.settings.transport_max_retries:
                    await asyncio.sleep(self.settings.transport_retry_backoff_seconds * attempt)
                    continue
                raise OdooRpcError(f"{rpc_label} request error: {exc}", response_text=last_text) from exc

        raise OdooRpcError(
            f"{rpc_label} gagal setelah retry. {last_error_message}",
            status_code=last_status,
            response_text=last_text,
        )

    def _emit_rpc_telemetry(
        self,
        call_context: RpcCallContext,
        started_ts: float,
        ended_ts: float,
        duration_ms: int,
        status: str,
        attempt: int,
        http_status: int,
        error_class: str,
        error_message: str,
    ) -> None:
        if self.rpc_telemetry_callback is None:
            return
        try:
            event = RpcTelemetry(
                started_at_utc=datetime.fromtimestamp(started_ts, tz=timezone.utc).isoformat().replace("+00:00", "Z"),
                ended_at_utc=datetime.fromtimestamp(ended_ts, tz=timezone.utc).isoformat().replace("+00:00", "Z"),
                duration_ms=max(0, int(duration_ms or 0)),
                status=status,
                attempt=max(1, int(attempt or 1)),
                http_status=max(0, int(http_status or 0)),
                error_class=error_class,
                error_message=error_message,
                model=call_context.model,
                method=call_context.method,
                stage=call_context.stage,
                excel_row=call_context.excel_row,
            )
            self.rpc_telemetry_callback(event)
        except Exception:
            return


class AsyncOdooClient:
    """Business-agnostic async CRUD wrapper over OdooTransport."""

    def __init__(self, transport: OdooTransport) -> None:
        self.transport = transport

    @property
    def config(self) -> OdooConfig:
        return self.transport.config

    @property
    def settings(self) -> RuntimeSettings:
        return self.transport.settings

    @property
    def logger(self) -> logging.Logger:
        return self.transport.logger

    @property
    def call_context(self) -> RpcCallContext:
        return self.transport.call_context

    @call_context.setter
    def call_context(self, value: RpcCallContext) -> None:
        self.transport.call_context = value

    @property
    def last_http_status(self) -> int:
        return self.transport.last_http_status

    @property
    def last_duration_ms(self) -> int:
        return self.transport.last_duration_ms

    @property
    def uid(self) -> int:
        return self.transport.uid

    async def close(self) -> None:
        await self.transport.close()

    async def __aenter__(self) -> "AsyncOdooClient":
        return self

    async def __aexit__(self, _exc_type, _exc, _tb) -> None:
        await self.close()

    def set_call_context(self, stage: str, model: str, method: str, excel_row: int = 0) -> None:
        self.call_context = RpcCallContext(stage=stage, model=model, method=method, excel_row=excel_row)

    async def ensure_login(self) -> int:
        return await self.transport.ensure_login()

    async def execute_kw(
        self,
        model: str,
        method: str,
        args: List[Any] | None = None,
        kwargs: Dict[str, Any] | None = None,
        stage: str = "",
        excel_row: int = 0,
        mutating: bool = False,
    ) -> Any:
        await self.ensure_login()
        args = args or []
        kwargs = kwargs or {}
        call_context = RpcCallContext(stage=stage, model=model, method=method, excel_row=excel_row)
        self.call_context = call_context
        payload = {
            "jsonrpc": "2.0",
            "method": "call",
            "params": {
                "service": "object",
                "method": "execute_kw",
                "args": [
                    self.config.database,
                    self.uid,
                    self.config.api_key,
                    model,
                    method,
                    args,
                    kwargs,
                ],
            },
            "id": 1,
        }
        body = await self.transport.request(
            payload=payload,
            rpc_label=f"{model}.{method}",
            use_write_lock=mutating,
            use_read_sem=not mutating,
            call_context=call_context,
        )
        return body.get("result")

    async def search_read(
        self,
        model: str,
        domain: List[Any],
        fields: Iterable[str] | None = None,
        limit: int | None = None,
        order: str | None = None,
        context: Dict[str, Any] | None = None,
        stage: str = "",
        excel_row: int = 0,
    ) -> List[Dict[str, Any]]:
        kwargs: Dict[str, Any] = {}
        if fields is not None:
            kwargs["fields"] = list(fields)
        if limit is not None:
            kwargs["limit"] = int(limit)
        if order:
            kwargs["order"] = order
        if context is not None:
            kwargs["context"] = context
        result = await self.execute_kw(
            model=model,
            method="search_read",
            args=[domain],
            kwargs=kwargs,
            stage=stage,
            excel_row=excel_row,
            mutating=False,
        )
        if not isinstance(result, list):
            return []
        return result

    async def read_group(
        self,
        model: str,
        domain: List[Any],
        fields: Iterable[str],
        groupby: Iterable[str],
        *,
        lazy: bool = True,
        context: Dict[str, Any] | None = None,
        stage: str = "",
        excel_row: int = 0,
    ) -> List[Dict[str, Any]]:
        kwargs: Dict[str, Any] = {"lazy": bool(lazy)}
        if context is not None:
            kwargs["context"] = context
        result = await self.execute_kw(
            model=model,
            method="read_group",
            args=[domain, list(fields), list(groupby)],
            kwargs=kwargs,
            stage=stage,
            excel_row=excel_row,
            mutating=False,
        )
        if not isinstance(result, list):
            return []
        return result

    async def search(
        self,
        model: str,
        domain: List[Any],
        context: Dict[str, Any] | None = None,
        stage: str = "",
        excel_row: int = 0,
    ) -> List[int]:
        kwargs: Dict[str, Any] = {}
        if context is not None:
            kwargs["context"] = context
        result = await self.execute_kw(
            model=model,
            method="search",
            args=[domain],
            kwargs=kwargs,
            stage=stage,
            excel_row=excel_row,
            mutating=False,
        )
        if not isinstance(result, list):
            return []
        ids: List[int] = []
        for item in result:
            try:
                ids.append(int(item))
            except (TypeError, ValueError):
                continue
        return ids

    async def read(
        self,
        model: str,
        ids: List[int],
        fields: Iterable[str] | None = None,
        context: Dict[str, Any] | None = None,
        stage: str = "",
        excel_row: int = 0,
    ) -> List[Dict[str, Any]]:
        kwargs: Dict[str, Any] = {}
        if fields is not None:
            kwargs["fields"] = list(fields)
        if context is not None:
            kwargs["context"] = context
        result = await self.execute_kw(
            model=model,
            method="read",
            args=[ids],
            kwargs=kwargs,
            stage=stage,
            excel_row=excel_row,
            mutating=False,
        )
        if not isinstance(result, list):
            return []
        return result

    async def create(
        self,
        model: str,
        values: Dict[str, Any],
        context: Dict[str, Any] | None = None,
        stage: str = "",
        excel_row: int = 0,
    ) -> int:
        kwargs: Dict[str, Any] = {}
        if context is not None:
            kwargs["context"] = context
        result = await self.execute_kw(
            model=model,
            method="create",
            args=[values],
            kwargs=kwargs,
            stage=stage,
            excel_row=excel_row,
            mutating=True,
        )
        try:
            return int(result)
        except (TypeError, ValueError):
            return 0

    async def write(
        self,
        model: str,
        ids: List[int],
        values: Dict[str, Any],
        context: Dict[str, Any] | None = None,
        stage: str = "",
        excel_row: int = 0,
    ) -> bool:
        kwargs: Dict[str, Any] = {}
        if context is not None:
            kwargs["context"] = context
        result = await self.execute_kw(
            model=model,
            method="write",
            args=[ids, values],
            kwargs=kwargs,
            stage=stage,
            excel_row=excel_row,
            mutating=True,
        )
        return bool(result)

    async def fields_get(
        self,
        model: str,
        attributes: List[str] | None = None,
        context: Dict[str, Any] | None = None,
        stage: str = "",
    ) -> Dict[str, Any]:
        kwargs: Dict[str, Any] = {}
        if attributes:
            kwargs["attributes"] = attributes
        if context is not None:
            kwargs["context"] = context
        result = await self.execute_kw(
            model=model,
            method="fields_get",
            args=[],
            kwargs=kwargs,
            stage=stage,
            mutating=False,
        )
        if isinstance(result, dict):
            return result
        return {}


class AsyncOdooJsonRpcClient(AsyncOdooClient):
    """Compatibility alias with legacy constructor shape."""

    def __init__(
        self,
        config: OdooConfig,
        settings: RuntimeSettings,
        logger: logging.Logger,
        max_concurrency: int | None = None,
        client: httpx.AsyncClient | None = None,
        rpc_telemetry_callback: Callable[[RpcTelemetry], None] | None = None,
    ) -> None:
        transport = OdooTransport(
            config=config,
            settings=settings,
            logger=logger,
            max_concurrency=max_concurrency,
            client=client,
            rpc_telemetry_callback=rpc_telemetry_callback,
        )
        super().__init__(transport=transport)
