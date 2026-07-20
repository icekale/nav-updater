import json
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from app.ocr.table_parser import extract_metric_rows


class JobTransport(httpx.BaseTransport):
    def handle_request(self, request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            assert request.headers["Authorization"] == "bearer token"
            return httpx.Response(200, json={"data": {"jobId": "job-1"}}, request=request)
        if str(request.url) == "https://paddleocr.aistudio-app.com/api/v2/ocr/jobs/job-1":
            return httpx.Response(
                200,
                json={
                    "data": {
                        "state": "done",
                        "resultUrl": {"jsonUrl": "https://result.test/job-1.json"},
                    }
                },
                request=request,
            )
        result = {
            "result": {
                "layoutParsingResults": [
                    {
                        "markdown": {
                            "text": """
<table><tr><td>管理人</td><td>产品名称</td><td>近一周(%)</td><td>MTD(%)</td></tr>
<tr><td>管理人A</td><td>产品A</td><td>1.23</td><td>-</td></tr>
<tr><td>管理人B</td><td></td><td>9.99</td><td>8.88</td></tr></table>
"""
                        }
                    }
                ]
            }
        }
        return httpx.Response(200, text=json.dumps(result) + "\n", request=request)


class FailedJobTransport(httpx.BaseTransport):
    def handle_request(self, request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200, json={"data": {"jobId": "job-1"}}, request=request)
        return httpx.Response(
            200,
            json={"data": {"state": "failed", "errorMsg": "quota exceeded"}},
            request=request,
        )


def test_paddle_service_converts_completed_html_table_to_tokens(tmp_path: Path) -> None:
    from app.ocr.paddle import PaddleOCRService

    image = tmp_path / "report.png"
    image.write_bytes(b"image")
    service = PaddleOCRService("token", client=httpx.Client(transport=JobTransport()))

    rows = extract_metric_rows(service.recognize_tiled(image))

    assert len(rows) == 1
    assert rows[0].product_name == "产品A"
    assert rows[0].metrics == {"weekly": Decimal("0.0123")}
    assert rows[0].blank_metrics == frozenset({"mtd"})


def test_paddle_service_raises_for_failed_job(tmp_path: Path) -> None:
    from app.ocr.paddle import PaddleOCRError, PaddleOCRService

    image = tmp_path / "report.png"
    image.write_bytes(b"image")
    service = PaddleOCRService("token", client=httpx.Client(transport=FailedJobTransport()))

    with pytest.raises(PaddleOCRError, match="quota exceeded"):
        service.recognize_tiled(image)


def test_create_ocr_service_uses_local_rapid_by_default() -> None:
    from app.config import Settings
    from app.ocr.engine import OCRService, create_ocr_service

    assert isinstance(create_ocr_service(Settings()), OCRService)


def test_create_ocr_service_requires_token_for_paddle() -> None:
    from app.config import Settings
    from app.ocr.engine import PaddleOCRConfigurationError, create_ocr_service

    with pytest.raises(PaddleOCRConfigurationError, match="PADDLE_OCR_TOKEN"):
        create_ocr_service(Settings(ocr_backend="paddle"))


def test_create_ocr_service_uses_paddle_when_configured() -> None:
    from app.config import Settings
    from app.ocr.engine import create_ocr_service
    from app.ocr.paddle import PaddleOCRService

    service = create_ocr_service(Settings(ocr_backend="paddle", paddle_ocr_token="token"))

    assert isinstance(service, PaddleOCRService)
