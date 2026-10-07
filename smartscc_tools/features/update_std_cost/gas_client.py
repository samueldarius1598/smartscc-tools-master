"""GAS readers for Update Standard Cost area/company map."""

from __future__ import annotations

import time
from typing import Any, Dict, Iterable, List, Tuple

import requests

from smartscc_tools.features.update_std_cost.parsers import normalize_company_id_key, normalize_config_text
from smartscc_tools.services.gas.credentials import load_gas_api_key


GAS_BASE_URL = (
    "https://script.google.com/macros/s/"
    "AKfycbwmRLmYx_A3xXmBIS8cEWdxmSqDRXG-iaOxwCV2--u91TW1jdJxY-ud25jX4zzO6Qgm/exec"
)
GAS_API_KEY = load_gas_api_key()
GAS_MAX_RETRIES = 3
GAS_RETRY_BACKOFF_SECONDS = 1.0
GAS_RETRYABLE_HTTP_STATUS = {408, 429, 500, 502, 503, 504}


def fetch_company_area_map(
    *,
    area_gid: int,
    session: requests.Session | None = None,
    timeout: Tuple[int, int] = (30, 30),
) -> Dict[str, str]:
    if not GAS_API_KEY:
        raise RuntimeError("API key GAS belum diatur. Lihat docs/github_handoff.md.")
    sess = session or requests.Session()
    params = {
        "key": GAS_API_KEY,
        "op": "auto",
        "type": "raw",
        "gid": str(area_gid),
    }
    response = _get_with_retry(sess, GAS_BASE_URL, params=params, timeout=timeout)
    if response is None or response.status_code != 200 or not response.text:
        return {}

    try:
        root = response.json()
    except ValueError:
        return {}
    if not root.get("ok") or not isinstance(root.get("values"), list):
        return {}
    return build_company_area_map_from_values(root["values"])


def build_company_area_map_from_values(values: Iterable[Iterable[Any]]) -> Dict[str, str]:
    rows = _normalize_values(values)
    if not rows:
        return {}

    max_col = max(len(row) for row in rows)
    matrix = [list(row) + [""] * (max_col - len(row)) for row in rows]
    row_count = len(matrix)

    header_row = 1
    col_company_id = 0
    col_area = 0
    for col in range(1, max_col + 1):
        raw = _cell(matrix, header_row, col)
        header = normalize_config_text(raw).lower()
        if "company id" in header or header in {"company_id", "companyid"}:
            col_company_id = col
        elif "area" in header:
            col_area = col

    start_row = 1
    if col_company_id > 0 or col_area > 0:
        start_row = header_row + 1
    if col_company_id == 0:
        col_company_id = _guess_company_id_column(matrix, start_row, max_col)
    if col_area == 0:
        col_area = _guess_area_column(col_company_id, max_col)
    if col_company_id <= 0 or col_area <= 0:
        return {}

    output: Dict[str, str] = {}
    for row_idx in range(start_row, row_count + 1):
        key = normalize_company_id_key(_cell(matrix, row_idx, col_company_id))
        if not key:
            continue
        area_value = str(_cell(matrix, row_idx, col_area)).strip()
        if area_value:
            output[key] = area_value
    return output


def _guess_company_id_column(matrix: List[List[Any]], start_row: int, col_count: int) -> int:
    sample_end = min(start_row + 9, len(matrix))
    best_col = 0
    best_score = 0
    for col in range(1, min(col_count, 2) + 1):
        score = 0
        for row_idx in range(start_row, sample_end + 1):
            text = str(_cell(matrix, row_idx, col)).strip()
            if not text:
                continue
            try:
                float(text)
                score += 1
            except ValueError:
                continue
        if score > best_score:
            best_score = score
            best_col = col
    return best_col


def _guess_area_column(company_id_col: int, col_count: int) -> int:
    if company_id_col == 2:
        if col_count >= 4:
            return 4
        if col_count >= 3:
            return 3
    elif company_id_col == 1:
        if col_count >= 3:
            return 3
    elif col_count >= 3:
        return 3
    return 0


def _normalize_values(values: Iterable[Iterable[Any]]) -> List[List[Any]]:
    output: List[List[Any]] = []
    for row in values:
        output.append(list(row) if not isinstance(row, list) else row)
    return output


def _cell(matrix: List[List[Any]], row: int, col: int) -> Any:
    if row < 1 or col < 1 or row > len(matrix):
        return ""
    current = matrix[row - 1]
    if col > len(current):
        return ""
    return current[col - 1]


def _get_with_retry(
    session: requests.Session,
    url: str,
    *,
    params: Dict[str, str] | None = None,
    timeout: Tuple[int, int] = (30, 30),
    max_retries: int = GAS_MAX_RETRIES,
    retry_backoff_seconds: float = GAS_RETRY_BACKOFF_SECONDS,
) -> requests.Response | None:
    retries = max(1, int(max_retries))
    for attempt in range(1, retries + 1):
        try:
            response = session.get(url, params=params, timeout=timeout)
        except requests.RequestException:
            response = None
        else:
            if int(response.status_code) not in GAS_RETRYABLE_HTTP_STATUS or attempt >= retries:
                return response
        if attempt < retries:
            time.sleep(retry_backoff_seconds * attempt)
    return response
