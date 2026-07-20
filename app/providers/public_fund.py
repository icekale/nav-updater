from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from ..config import get_settings
from ..domain.matching import normalize_name
from ..domain.types import NavPoint


class ProviderError(RuntimeError):
    pass


@dataclass(frozen=True)
class PublicFundRecord:
    code: str
    name: str


class PublicFundProvider:
    endpoint = "https://api.fund.eastmoney.com/f10/lsjz"
    catalog_endpoint = "https://fund.eastmoney.com/js/fundcode_search.js"
    history_start_date = "2018-01-01"
    history_page_size = 20

    def __init__(self, client: httpx.Client | None = None) -> None:
        self.client = client or httpx.Client(
            timeout=get_settings().public_fund_timeout_seconds,
            headers={"User-Agent": "nav-updater/0.1", "Referer": "https://fund.eastmoney.com/"},
        )
        self._catalog: list[PublicFundRecord] | None = None

    def resolve_by_name(self, product_name: str) -> PublicFundRecord | None:
        records = self._catalog_records()
        normalized = normalize_name(product_name)
        exact = [record for record in records if normalize_name(record.name) == normalized]
        if exact:
            return _unique_record(exact)
        alias_key = _fund_name_key(product_name)
        aliases = [record for record in records if _fund_name_key(record.name) == alias_key]
        return _unique_record(aliases)

    def fetch_history(self, product_code: str, start_date: date | None = None) -> list[NavPoint]:
        rows = self._fetch_history_rows(
            product_code,
            start_date or date.fromisoformat(self.history_start_date),
        )
        points: list[NavPoint] = []
        imported_at = datetime.now(UTC).isoformat()
        for row in rows:
            try:
                day = date.fromisoformat(str(row["FSRQ"]))
                nav = Decimal(str(row["LJJZ"]))
            except (KeyError, InvalidOperation, ValueError) as exc:
                raise ProviderError(f"invalid NAV row for {product_code}") from exc
            if nav > 0:
                points.append(NavPoint(day, nav, f"eastmoney:{product_code}:{imported_at}"))
        return points

    def _fetch_history_rows(self, product_code: str, start_date: date) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        page_index = 1
        while True:
            try:
                response = self.client.get(
                    self.endpoint,
                    params={
                        "fundCode": product_code,
                        "pageIndex": page_index,
                        "pageSize": self.history_page_size,
                        "startDate": start_date.isoformat(),
                        "endDate": date.today().isoformat(),
                    },
                )
                response.raise_for_status()
                payload = response.json()
            except (httpx.HTTPError, ValueError) as exc:
                raise ProviderError(f"failed to fetch public fund {product_code}") from exc
            page_rows = self._extract_rows(payload, product_code)
            rows.extend(page_rows)
            total = payload.get("TotalCount") if isinstance(payload, dict) else None
            if (
                not isinstance(total, int)
                or len(rows) >= total
                or len(page_rows) < self.history_page_size
            ):
                return rows
            page_index += 1

    def _catalog_records(self) -> list[PublicFundRecord]:
        if self._catalog is not None:
            return self._catalog
        try:
            response = self.client.get(self.catalog_endpoint)
            response.raise_for_status()
            content = response.text.lstrip("\ufeff")
            match = re.fullmatch(r"\s*var\s+r\s*=\s*(\[.*\])\s*;?\s*", content, re.DOTALL)
            if match is None:
                raise ValueError("missing fund catalog")
            raw_records = json.loads(match.group(1))
        except (httpx.HTTPError, ValueError, json.JSONDecodeError) as exc:
            raise ProviderError("failed to fetch public fund catalog") from exc
        if not isinstance(raw_records, list):
            raise ProviderError("invalid public fund catalog")
        self._catalog = [
            PublicFundRecord(code=str(record[0]).strip(), name=str(record[2]).strip())
            for record in raw_records
            if isinstance(record, list)
            and len(record) >= 3
            and str(record[0]).strip()
            and str(record[2]).strip()
        ]
        return self._catalog

    @staticmethod
    def _extract_rows(payload: Any, product_code: str) -> list[dict[str, Any]]:
        if not isinstance(payload, dict):
            raise ProviderError(f"invalid response for {product_code}")
        data = payload.get("Data")
        if not isinstance(data, dict) or not isinstance(data.get("LSJZList"), list):
            raise ProviderError(f"missing NAV list for {product_code}")
        return [row for row in data["LSJZList"] if isinstance(row, dict)]


def _fund_name_key(value: str) -> str:
    return normalize_name(value).replace("灵活配置", "").replace("型证券投资基金", "")


def _unique_record(records: list[PublicFundRecord]) -> PublicFundRecord | None:
    by_code = {record.code: record for record in records}
    return next(iter(by_code.values())) if len(by_code) == 1 else None
