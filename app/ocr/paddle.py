from __future__ import annotations

import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
from lxml import html

from .engine import OCRToken


class PaddleOCRError(RuntimeError):
    pass


class PaddleOCRService:
    job_url = "https://paddleocr.aistudio-app.com/api/v2/ocr/jobs"
    model = "PaddleOCR-VL-1.6"

    def __init__(
        self,
        token: str,
        *,
        timeout_seconds: float = 120.0,
        poll_interval_seconds: float = 2.0,
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if not token.strip():
            raise PaddleOCRError("Paddle OCR token is required")
        self.client = client or httpx.Client(timeout=timeout_seconds)
        self.timeout_seconds = timeout_seconds
        self.poll_interval_seconds = poll_interval_seconds
        self.sleep = sleep
        self.monotonic = monotonic
        self.headers = {"Authorization": f"bearer {token}"}

    def recognize_tiled(self, image: str | Path) -> list[OCRToken]:
        image_path = Path(image)
        if not image_path.is_file():
            raise PaddleOCRError(f"Paddle OCR image is missing: {image_path}")
        job_id = self._submit(image_path)
        return tokens_from_markdown(self._wait_for_markdown(job_id))

    def _submit(self, image_path: Path) -> str:
        try:
            with image_path.open("rb") as image:
                response = self.client.post(
                    self.job_url,
                    headers=self.headers,
                    data={
                        "model": self.model,
                        "optionalPayload": json.dumps(
                            {
                                "useDocOrientationClassify": False,
                                "useDocUnwarping": False,
                                "useChartRecognition": False,
                            }
                        ),
                    },
                    files={"file": (image_path.name, image, "image/png")},
                )
            response.raise_for_status()
            payload = response.json()
            job_id = payload["data"]["jobId"]
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            raise PaddleOCRError("failed to submit Paddle OCR job") from exc
        if not isinstance(job_id, str) or not job_id:
            raise PaddleOCRError("Paddle OCR response is missing a job ID")
        return job_id

    def _wait_for_markdown(self, job_id: str) -> str:
        deadline = self.monotonic() + self.timeout_seconds
        while True:
            payload = self._job_payload(job_id)
            data = payload.get("data") if isinstance(payload, dict) else None
            state = data.get("state") if isinstance(data, dict) else None
            if state == "done":
                result_url = data.get("resultUrl")
                json_url = result_url.get("jsonUrl") if isinstance(result_url, dict) else None
                if not isinstance(json_url, str) or not json_url:
                    raise PaddleOCRError("Paddle OCR completed without a result URL")
                return self._download_markdown(json_url)
            if state == "failed":
                error_message = data.get("errorMsg") if isinstance(data, dict) else None
                raise PaddleOCRError(f"Paddle OCR job failed: {error_message or 'unknown error'}")
            if state not in {"pending", "running"}:
                raise PaddleOCRError("Paddle OCR returned an invalid job state")
            if self.monotonic() >= deadline:
                raise PaddleOCRError("Paddle OCR job timed out")
            self.sleep(self.poll_interval_seconds)

    def _job_payload(self, job_id: str) -> dict[str, Any]:
        try:
            response = self.client.get(f"{self.job_url}/{job_id}", headers=self.headers)
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise PaddleOCRError("failed to query Paddle OCR job") from exc
        if not isinstance(payload, dict):
            raise PaddleOCRError("Paddle OCR job response is invalid")
        return payload

    def _download_markdown(self, json_url: str) -> str:
        try:
            response = self.client.get(json_url)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise PaddleOCRError("failed to download Paddle OCR result") from exc

        markdown: list[str] = []
        for line in response.text.splitlines():
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
                layouts = payload["result"]["layoutParsingResults"]
            except (KeyError, TypeError, json.JSONDecodeError) as exc:
                raise PaddleOCRError("Paddle OCR result is malformed") from exc
            if not isinstance(layouts, list):
                raise PaddleOCRError("Paddle OCR result is malformed")
            for layout in layouts:
                text = (
                    layout.get("markdown", {}).get("text") if isinstance(layout, dict) else None
                )
                if isinstance(text, str) and text.strip():
                    markdown.append(text)
        if not markdown:
            raise PaddleOCRError("Paddle OCR result contains no table text")
        return "\n".join(markdown)


def tokens_from_markdown(markdown: str) -> list[OCRToken]:
    try:
        root = html.fromstring(markdown)
    except (TypeError, ValueError) as exc:
        raise PaddleOCRError("Paddle OCR markdown is invalid") from exc

    tokens: list[OCRToken] = []
    for table_index, table in enumerate(root.xpath("//table")):
        rows = table.xpath(".//tr")
        if not rows:
            continue
        headers = _cell_texts(rows[0])
        product_index = _product_name_index(headers)
        if product_index is None or not any(_is_metric_header(header) for header in headers):
            continue
        base_top = table_index * 10000.0
        tokens.extend(_row_tokens(headers, 0, base_top))
        for row_index, row in enumerate(rows[1:], start=1):
            cells = _cell_texts(row)
            if product_index >= len(cells) or not cells[product_index]:
                continue
            tokens.extend(_row_tokens(cells, row_index, base_top))
    return tokens


def _cell_texts(row: Any) -> list[str]:
    return [" ".join(cell.text_content().split()) for cell in row.xpath("./th|./td")]


def _product_name_index(headers: list[str]) -> int | None:
    normalized = {"产品名称", "产品", "名称"}
    return next(
        (index for index, header in enumerate(headers) if header.replace(" ", "") in normalized),
        None,
    )


def _is_metric_header(header: str) -> bool:
    normalized = header.replace(" ", "").lower()
    return (
        normalized.startswith(
            ("近一周", "mtd", "ytd", "2019", "2020", "2021", "2022", "2023", "2024", "2025")
        )
        or (normalized.startswith("近一年") and ("夏普" in normalized or "回撤" in normalized))
    )


def _row_tokens(cells: list[str], row_index: int, base_top: float) -> list[OCRToken]:
    top = base_top + row_index * 30.0
    return [
        OCRToken(
            text=text,
            box=(
                (index * 200.0, top),
                (index * 200.0 + 150.0, top),
                (index * 200.0 + 150.0, top + 20.0),
                (index * 200.0, top + 20.0),
            ),
            confidence=1.0,
        )
        for index, text in enumerate(cells)
        if text
    ]
