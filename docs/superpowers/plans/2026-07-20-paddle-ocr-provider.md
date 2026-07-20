# PaddleOCR-VL Provider Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an opt-in PaddleOCR-VL backend that supplies parser-compatible table tokens while leaving RapidOCR as the default.

**Architecture:** `app.ocr.paddle` owns the external job lifecycle and table-to-token conversion. `app.ocr.engine.create_ocr_service()` selects Rapid or Paddle from explicit settings; `process_run()` keeps its existing parser and matching flow. No token or screenshot result is persisted beyond the existing run input.

**Tech Stack:** Python 3.12, httpx, lxml, pydantic-settings, pytest.

---

### Task 1: Translate Paddle HTML Tables To Existing OCR Tokens

**Files:**
- Create: `app/ocr/paddle.py`
- Create: `tests/unit/test_paddle_ocr.py`

- [ ] **Step 1: Write the failing adapter test**

```python
def test_paddle_service_converts_completed_html_table_to_tokens(tmp_path: Path) -> None:
    image = tmp_path / "report.png"
    image.write_bytes(b"image")
    service = PaddleOCRService("token", client=httpx.Client(transport=JobTransport()))
    rows = extract_metric_rows(service.recognize_tiled(image))
    assert rows[0].product_name == "产品A"
    assert rows[0].metrics == {"weekly": Decimal("0.0123")}
    assert rows[0].blank_metrics == frozenset({"mtd"})
```

`JobTransport` returns a job ID for `POST /jobs`, an immediate `done` response for `GET /jobs/job-1`, and one JSONL result containing an HTML table with 产品名称、近一周、MTD header cells. Include an HTML row without a product name and assert it does not create a parsed row.

- [ ] **Step 2: Verify red**

Run: `/Users/kale/Documents/熊总/.venv/bin/python -m pytest tests/unit/test_paddle_ocr.py::test_paddle_service_converts_completed_html_table_to_tokens -q`

Expected: FAIL because `PaddleOCRService` does not exist.

- [ ] **Step 3: Implement the smallest adapter**

```python
class PaddleOCRService:
    job_url = "https://paddleocr.aistudio-app.com/api/v2/ocr/jobs"

    def recognize_tiled(self, image: str | Path) -> list[OCRToken]:
        job_id = self._submit(Path(image))
        markdown = self._wait_for_markdown(job_id)
        return tokens_from_markdown(markdown)
```

Use `httpx.Client`, request authorization, submit a multipart file with `PaddleOCR-VL-1.6`, poll `pending`/`running` until `done`, and parse each nonempty JSONL line. Convert only valid HTML table rows with a nonempty 产品名称 cell to synthetic `OCRToken`s using table, row, and column offsets. Preserve `-` cells as tokens; do not rewrite product names.

- [ ] **Step 4: Write and verify failure handling**

```python
def test_paddle_service_raises_for_failed_job(tmp_path: Path) -> None:
    service = PaddleOCRService("token", client=httpx.Client(transport=FailedJobTransport()))
    with pytest.raises(PaddleOCRError, match="quota exceeded"):
        service.recognize_tiled(tmp_path / "report.png")
```

Run: `/Users/kale/Documents/熊总/.venv/bin/python -m pytest tests/unit/test_paddle_ocr.py -q`

Expected: PASS.

### Task 2: Select The Backend From Configuration

**Files:**
- Modify: `app/config.py`
- Modify: `app/ocr/engine.py`
- Modify: `app/jobs/processor.py`
- Modify: `tests/unit/test_paddle_ocr.py`

- [ ] **Step 1: Write failing selector tests**

```python
def test_create_ocr_service_uses_local_rapid_by_default() -> None:
    assert isinstance(create_ocr_service(Settings()), OCRService)

def test_create_ocr_service_requires_token_for_paddle() -> None:
    with pytest.raises(PaddleOCRConfigurationError, match="PADDLE_OCR_TOKEN"):
        create_ocr_service(Settings(ocr_backend="paddle"))
```

- [ ] **Step 2: Verify red**

Run: `/Users/kale/Documents/熊总/.venv/bin/python -m pytest tests/unit/test_paddle_ocr.py -k create_ocr_service -q`

Expected: FAIL because settings and the selector do not exist.

- [ ] **Step 3: Add explicit opt-in settings and factory**

```python
class Settings(BaseSettings):
    ocr_backend: Literal["rapid", "paddle"] = "rapid"
    paddle_ocr_token: str = ""
    paddle_ocr_timeout_seconds: float = 120.0
```

Define `OCRRecognizer` with `recognize_tiled(path) -> list[OCRToken]`. Add `create_ocr_service(settings)` to return `OCRService` for `rapid`, otherwise require the token and create `PaddleOCRService`. Change only the default `process_run()` construction to call the factory; injected test doubles retain the same method shape.

- [ ] **Step 4: Verify green**

Run: `/Users/kale/Documents/熊总/.venv/bin/python -m pytest tests/unit/test_paddle_ocr.py -q`

Expected: PASS.

### Task 3: Document Safe Operation And Verify The Contract

**Files:**
- Modify: `.env.example`
- Modify: `README.md`
- Test: full suite and lint

- [ ] **Step 1: Add configuration examples without a secret**

```dotenv
OCR_BACKEND=rapid
PADDLE_OCR_TOKEN=
PADDLE_OCR_TIMEOUT_SECONDS=120
```

Document that selecting `paddle` sends screenshots to an external service, that the token is set only in Unraid `.env`, and that `OCR_BACKEND=rapid` plus `docker compose up -d --build` rolls back to local OCR.

- [ ] **Step 2: Run verification**

Run: `/Users/kale/Documents/熊总/.venv/bin/python -m pytest -q`

Expected: PASS.

Run: `/Users/kale/Documents/熊总/.venv/bin/ruff check .`

Expected: `All checks passed!`

- [ ] **Step 3: Commit**

```bash
git add app/config.py app/ocr/engine.py app/ocr/paddle.py app/jobs/processor.py tests/unit/test_paddle_ocr.py .env.example README.md docs/superpowers/specs/2026-07-20-paddle-ocr-provider-design.md docs/superpowers/plans/2026-07-20-paddle-ocr-provider.md
git commit -m "feat: add opt-in Paddle OCR provider"
```
