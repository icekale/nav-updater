# Mixed Fund Matching Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Process long performance screenshots reliably, then automatically resolve only uniquely identified public-fund names when a screenshot does not cover an Excel product.

**Architecture:** `OCRService` will tile tall source images and return original-image coordinates, so the existing table parser receives legible tokens without a new persistence model. `PublicFundProvider` will parse the public fund-code directory and resolve names conservatively. `process_run` will use screenshot values first, then the existing catalog, then a newly resolved public product; it will persist only confirmed public mappings.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy, httpx, RapidOCR, OpenCV, pytest, ruff.

---

## File Structure

- Modify: `app/ocr/engine.py` — crop tall images, translate and deduplicate OCR tokens.
- Modify: `app/providers/public_fund.py` — parse Eastmoney's code directory and expose a unique public-fund resolver.
- Modify: `app/catalog.py` — create/reuse an automatically resolved public product and retain the submitted Excel name as an alias.
- Modify: `app/jobs/processor.py` — apply screenshot-first matching and use the resolver only for uncovered rows.
- Modify: `app/main.py` — persist the automatic catalog action in the existing audit log.
- Modify: `tests/unit/test_table_parser.py` — cover parser behavior with tiled OCR output.
- Create: `tests/unit/test_ocr_engine.py` — cover token offsets and overlap deduplication without loading an ONNX model.
- Modify: `tests/integration/test_provider.py` — cover public-code directory parsing and unique-name resolution.
- Modify: `tests/integration/test_jobs.py` — cover screenshot matching with an empty catalog and public fallback persistence.
- Modify: `tests/e2e/test_lan_flow.py` — keep the existing browser workflow deterministic without a public network request.

### Task 1: Add tiled OCR with original-image coordinates

**Files:**
- Modify: `app/ocr/engine.py`
- Create: `tests/unit/test_ocr_engine.py`
- Test: `tests/unit/test_ocr_engine.py`

- [ ] **Step 1: Write the failing tiled-OCR test**

Create a fake image with 6000 rows and monkeypatch `cv2.imread`. Replace `OCRService.recognize` with a function that returns a token at the bottom of the first tile and the top of the second tile for the same logical text, plus a unique token per tile.

```python
def test_recognize_tiled_offsets_tokens_and_keeps_best_overlap_token(monkeypatch) -> None:
    image = np.zeros((6000, 20, 3), dtype=np.uint8)
    monkeypatch.setattr("app.ocr.engine.cv2.imread", lambda _: image)
    service = OCRService()
    starts = iter([0, 2472, 4944])

    def recognize(_crop):
        start = next(starts)
        if start == 0:
            return [token("重复", top=2472, confidence=0.80), token("首段", top=0)]
        if start == 2472:
            return [token("重复", top=0, confidence=0.99), token("中段", top=10)]
        return [token("末段", top=10)]

    monkeypatch.setattr(service, "recognize", recognize)

    result = service.recognize_tiled("long.png", tile_height=2600, overlap=128)

    assert [(item.text, item.top, item.confidence) for item in result] == [
        ("首段", 0.0, 0.99),
        ("重复", 2472.0, 0.99),
        ("中段", 2482.0, 0.99),
        ("末段", 4954.0, 0.99),
    ]
```

- [ ] **Step 2: Run the test to verify it fails**

Run:

```bash
/Users/kale/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m pytest tests/unit/test_ocr_engine.py -q
```

Expected: FAIL because `OCRService.recognize_tiled` does not exist.

- [ ] **Step 3: Implement the smallest tiled OCR API**

Add `recognize_tiled(self, image, tile_height=2600, overlap=128)` to `OCRService`. It must load the input with `cv2.imread`, call existing `recognize()` for each tile with start positions `0, tile_height-overlap, ...`, create new `OCRToken` values with the tile start added to each box y-coordinate, then deduplicate equal text whose translated `left` and `top` differ by at most two pixels, keeping the higher confidence token and sorting the returned tokens by top then left. For an image at or below `tile_height`, call `recognize()` once. Reject invalid dimensions with `ValueError`.

```python
def _shift_token(token: OCRToken, offset_y: int) -> OCRToken:
    return OCRToken(
        text=token.text,
        box=tuple((x, y + offset_y) for x, y in token.box),
        confidence=token.confidence,
    )

def _is_same_token(left: OCRToken, right: OCRToken) -> bool:
    return (
        left.text == right.text
        and abs(left.left - right.left) <= 2
        and abs(left.top - right.top) <= 2
    )
```

- [ ] **Step 4: Run the focused test to verify it passes**

Run the command from Step 2.

Expected: PASS with one test.

- [ ] **Step 5: Commit the tiled OCR behavior**

```bash
git add app/ocr/engine.py tests/unit/test_ocr_engine.py
git commit -m "feat: tile tall OCR images"
```

### Task 2: Resolve a public fund only when its name is unique

**Files:**
- Modify: `app/providers/public_fund.py`
- Modify: `tests/integration/test_provider.py`
- Test: `tests/integration/test_provider.py`

- [ ] **Step 1: Write failing directory-resolution tests**

Add a `MockTransport` response containing this JavaScript body:

```python
body = (
    'var r = [["001856", "YFDHBZTHHA", "易方达环保主题混合A", '
    '"混合型-灵活", "YIFANGDAHUANBAOZHUTIHUNHEA"]];'
)
```

Assert that `resolve_by_name("易方达环保主题灵活配置混合A")` returns a record with code `001856`. Add a duplicate alias case with two different codes and assert it returns `None`. Add malformed JavaScript and HTTP 503 cases asserting `ProviderError`.

- [ ] **Step 2: Run the provider tests to verify they fail**

Run:

```bash
/Users/kale/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m pytest tests/integration/test_provider.py -q
```

Expected: FAIL because `resolve_by_name` and its record type do not exist.

- [ ] **Step 3: Implement unique public-name resolution**

In `app/providers/public_fund.py`, add `PublicFundRecord(code: str, name: str)` and `PublicFundProvider.catalog_endpoint = "https://fund.eastmoney.com/js/fundcode_search.js"`. Implement `resolve_by_name(name)` by downloading the JavaScript once per provider instance, parsing the JSON array after `var r =`, and evaluating candidates in two passes: first exact `normalize_name`; then the unique alias key obtained by removing only `灵活配置` and `型证券投资基金`. Keep share suffixes unchanged. Return `None` whenever a pass has zero or more than one distinct code.

```python
def _fund_name_key(value: str) -> str:
    normalized = normalize_name(value)
    return normalized.replace("灵活配置", "").replace("型证券投资基金", "")

def _unique_record(records: list[PublicFundRecord]) -> PublicFundRecord | None:
    by_code = {record.code: record for record in records}
    return next(iter(by_code.values())) if len(by_code) == 1 else None
```

Use the provider's existing `httpx.Client`, headers, timeout, and `ProviderError` style. Do not use the suggestion API or fuzzy matching.

- [ ] **Step 4: Run the focused provider tests to verify they pass**

Run the command from Step 2.

Expected: PASS with all provider tests.

- [ ] **Step 5: Commit the resolver**

```bash
git add app/providers/public_fund.py tests/integration/test_provider.py
git commit -m "feat: resolve unique public fund names"
```

### Task 3: Persist confirmed public mappings and make the processor screenshot-first

**Files:**
- Modify: `app/catalog.py`
- Modify: `app/jobs/processor.py`
- Modify: `app/main.py`
- Modify: `tests/integration/test_jobs.py`
- Modify: `tests/e2e/test_lan_flow.py`
- Test: `tests/integration/test_jobs.py`

- [ ] **Step 1: Write the two failing mixed-source integration tests**

Add a `FakeTiledOCR` whose `recognize_tiled()` returns `OCRToken` values that `extract_metric_rows()` converts into a row for `仁桥金选泽源5B`, and invoke `process_run()` with no products in the database. Assert the matching item gets `match_source == "image"`, its metrics are sent to the adapter, and its `product_id` remains `None`.

Add a second run with no image rows and a fake provider where `resolve_by_name("易方达环保主题灵活配置混合A")` returns `PublicFundRecord("001856", "易方达环保主题混合A")` and `fetch_history()` returns existing fixture points. Assert that the item gets `match_source == "public_provider"`, a `Product` with code `001856`, type `public`, and `historical_names == ["易方达环保主题灵活配置混合A"]` is persisted.

Update the two existing LAN-flow catalog CSV fixtures to include all six names from `net_value_template.xlsx` as private test products. That keeps the browser test focused on local upload/process/download behavior and prevents the new public resolver from making an external request during unit test runs.

- [ ] **Step 2: Run the integration tests to verify they fail**

Run:

```bash
/Users/kale/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m pytest tests/integration/test_jobs.py -q
```

Expected: FAIL because the processor calls `recognize()` directly and has no resolver fallback or automatic product persistence.

- [ ] **Step 3: Implement only the required processing flow**

Add an `ensure_public_product(session, record, source_name)` helper in `app/catalog.py`. It must reuse a matching code, append the source name to `historical_names` if absent, and otherwise create `Product(product_name=record.name, product_code=record.code, product_type="public", historical_names=[source_name])`.

In `process_run()`:

1. Call `ocr_service.recognize_tiled()` for every uploaded image.
2. Keep `_find_image_row()`'s direct normalized-name fallback so the image can match when `products` is empty.
3. If no image row and no existing catalog product was found, call `provider.resolve_by_name(name)`. For a record, call `ensure_public_product`, append the product to the in-memory list, then use existing `fetch_history()` and metric calculation.
4. If resolution returns `None`, retain the original Excel values and set the reason to `截图未找到对应产品，公募名称未能唯一确认基金代码`.
5. Preserve existing manual-review bypass and all existing public-provider error behavior.

Add an optional `actor_id` argument to `process_run()`. When `ensure_public_product()` creates a record, write `AuditLog(action="resolve_public_product")` from the processor with the supplied actor or the batch operator, the public code, canonical name, and original Excel name; do not log any external payload. Have the web route pass the current user's ID and let the worker use the batch operator by default.

- [ ] **Step 4: Run the focused integration tests to verify they pass**

Run the command from Step 2.

Expected: PASS with all job tests.

- [ ] **Step 5: Commit mixed-source processing**

```bash
git add app/catalog.py app/jobs/processor.py app/main.py tests/integration/test_jobs.py
git commit -m "feat: process mixed fund update batches"
```

### Task 4: Run the complete regression and container verification

**Files:**
- Modify: `README.md`
- Test: all tests, ruff, Docker image runtime check

- [ ] **Step 1: Document behavior and operating expectation**

Add a README note under the update workflow: long PNG/JPEG screenshots may contain both public and private products; public code lookup is attempted only for screenshot-missing rows and only when one exact or supported historical name candidate exists. State that old completed runs are not reprocessed automatically.

- [ ] **Step 2: Run all local checks**

Run:

```bash
/Users/kale/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m pytest -q
/Users/kale/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m ruff check .
docker build --platform linux/amd64 -t nav-updater:mixed-fund-matching .
docker run --rm nav-updater:mixed-fund-matching python -c 'from app.ocr.engine import OCRService; from app.providers.public_fund import PublicFundProvider; print(OCRService.__name__, PublicFundProvider.__name__)'
```

Expected: all tests pass, ruff reports no violations, the amd64 image builds, and the runtime import command exits 0.

- [ ] **Step 3: Commit documentation and verification changes**

```bash
git add README.md
git commit -m "docs: explain mixed fund matching"
```

- [ ] **Step 4: Deploy and test a new Unraid batch**

Push the completed branch, fast-forward the deployment checkout, rebuild `app` and `worker` with `docker compose up -d --build`, then verify `/healthz`, container status, and the long screenshot with a newly created batch. Do not alter update runs 1 or 2.
