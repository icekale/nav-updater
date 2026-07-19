from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from ..config import get_settings
from ..domain.types import NavPoint


class ProviderError(RuntimeError):
    pass


class PublicFundProvider:
    endpoint = "https://api.fund.eastmoney.com/f10/lsjz"

    def __init__(self, client: httpx.Client | None = None) -> None:
        self.client = client or httpx.Client(
            timeout=get_settings().public_fund_timeout_seconds,
            headers={"User-Agent": "nav-updater/0.1", "Referer": "https://fund.eastmoney.com/"},
        )

    def fetch_history(self, product_code: str) -> list[NavPoint]:
        try:
            response = self.client.get(
                self.endpoint,
                params={"fundCode": product_code, "pageIndex": 1, "pageSize": 1000},
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise ProviderError(f"failed to fetch public fund {product_code}") from exc
        rows = self._extract_rows(payload, product_code)
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

    @staticmethod
    def _extract_rows(payload: Any, product_code: str) -> list[dict[str, Any]]:
        if not isinstance(payload, dict):
            raise ProviderError(f"invalid response for {product_code}")
        data = payload.get("Data")
        if not isinstance(data, dict) or not isinstance(data.get("LSJZList"), list):
            raise ProviderError(f"missing NAV list for {product_code}")
        return [row for row in data["LSJZList"] if isinstance(row, dict)]
