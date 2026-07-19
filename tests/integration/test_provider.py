import json
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from app.providers.public_fund import ProviderError, PublicFundProvider


class MockTransport(httpx.BaseTransport):
    def __init__(self, payload: object, status_code: int = 200) -> None:
        self.payload = payload
        self.status_code = status_code

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        return httpx.Response(self.status_code, json=self.payload, request=request)


def test_public_provider_normalizes_fixture() -> None:
    payload = json.loads(Path("tests/fixtures/eastmoney_response.json").read_text())
    provider = PublicFundProvider(httpx.Client(transport=MockTransport(payload)))
    points = provider.fetch_history("001856")
    assert points[0].date.isoformat() == "2026-07-17"
    assert points[0].value == Decimal("1.1500")
    assert points[0].source.startswith("eastmoney:001856:")


def test_public_provider_rejects_malformed_payload() -> None:
    provider = PublicFundProvider(httpx.Client(transport=MockTransport({"Data": {}})))
    with pytest.raises(ProviderError):
        provider.fetch_history("001856")


def test_public_provider_rejects_http_error() -> None:
    provider = PublicFundProvider(httpx.Client(transport=MockTransport({}, status_code=503)))
    with pytest.raises(ProviderError):
        provider.fetch_history("001856")
