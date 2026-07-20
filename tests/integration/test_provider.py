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


class TextMockTransport(httpx.BaseTransport):
    def __init__(self, body: str, status_code: int = 200) -> None:
        self.body = body
        self.status_code = status_code

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        return httpx.Response(self.status_code, text=self.body, request=request)


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


def test_public_provider_resolves_unique_historical_name() -> None:
    catalog = json.dumps(
        [["001856", "YFDHBZTHHA", "易方达环保主题混合A", "混合型-灵活", "YFDHBZTHHA"]],
        ensure_ascii=False,
    )
    transport = TextMockTransport(f"\ufeffvar r = {catalog};")
    provider = PublicFundProvider(httpx.Client(transport=transport))

    record = provider.resolve_by_name("易方达环保主题灵活配置混合A")

    assert record is not None
    assert record.code == "001856"
    assert record.name == "易方达环保主题混合A"


def test_public_provider_rejects_ambiguous_historical_name() -> None:
    catalog = json.dumps(
        [
            ["000001", "ONE", "示例混合A", "混合型", "ONE"],
            ["000002", "TWO", "示例灵活配置混合A", "混合型", "TWO"],
        ],
        ensure_ascii=False,
    )
    provider = PublicFundProvider(httpx.Client(transport=TextMockTransport(f"var r = {catalog};")))

    assert provider.resolve_by_name("示例型证券投资基金混合A") is None


def test_public_provider_rejects_invalid_catalog_response() -> None:
    provider = PublicFundProvider(httpx.Client(transport=TextMockTransport("var r = invalid;")))

    with pytest.raises(ProviderError):
        provider.resolve_by_name("易方达环保主题灵活配置混合A")


def test_public_provider_rejects_catalog_http_error() -> None:
    provider = PublicFundProvider(httpx.Client(transport=TextMockTransport("", status_code=503)))

    with pytest.raises(ProviderError):
        provider.resolve_by_name("易方达环保主题灵活配置混合A")
