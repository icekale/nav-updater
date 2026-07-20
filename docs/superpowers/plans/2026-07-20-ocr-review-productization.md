# OCR Review Productization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Parse each repeated performance-report table with its own header and make the OCR review workflow easier to operate.

**Architecture:** The OCR adapter remains a pure transformation from OCR tokens to `OCRMetricRow`, but iterates independent header blocks. The web layer derives summaries from existing `RunItem` fields, avoiding a migration or historical-data rewrite.

**Tech Stack:** Python 3.12, FastAPI, Jinja2, SQLAlchemy, pytest, CSS.

## Global Constraints

- Preserve the existing Excel output contract and `RunItem` persistence fields.
- Do not add automatic fuzzy matching.
- Treat blank source cells as missing and route them to review.
- Do not add a migration or external service.
- Require each upload batch to contain a single report date.

---

### Task 1: Parse Repeated OCR Header Blocks

**Files:**
- Modify: `app/ocr/table_parser.py`
- Test: `tests/unit/test_table_parser.py`

**Interfaces:**
- Consumes: `Iterable[OCRToken]`
- Produces: existing `list[OCRMetricRow]` from `extract_metric_rows(tokens)`

- [ ] **Step 1: Write the failing shifted-header test**

```python
def test_extract_metric_rows_uses_each_repeated_header_layout() -> None:
    rows = extract_metric_rows([
        token("产品名称", 10, 10), token("近一周(%)", 100, 10),
        token("产品A", 10, 50), token("1.00%", 100, 50),
        token("产品名称", 500, 100), token("近一周(%)", 800, 100),
        token("产品B", 500, 140), token("2.00%", 800, 140),
    ])
    assert [(row.product_name, row.metrics) for row in rows] == [
        ("产品A", {"weekly": Decimal("0.01")}),
        ("产品B", {"weekly": Decimal("0.02")}),
    ]
```

- [ ] **Step 2: Verify red**

Run: `/Users/kale/Documents/熊总/.venv/bin/python -m pytest tests/unit/test_table_parser.py::test_extract_metric_rows_uses_each_repeated_header_layout -q`

Expected: FAIL because the current parser reuses the first header positions.

- [ ] **Step 3: Implement minimal block discovery**

```python
def _header_blocks(rows: list[ParsedRow]) -> list[tuple[int, dict[str, float]]]:
    blocks = []
    for index, row in enumerate(rows):
        headers = _valid_headers(row)
        if headers is not None:
            headers.update(_supplement_headers(rows, index, headers))
            blocks.append((index, headers))
    return blocks
```

Make `extract_metric_rows` parse rows from each header through the next header using that block's positions.

- [ ] **Step 4: Verify green**

Run: `/Users/kale/Documents/熊总/.venv/bin/python -m pytest tests/unit/test_table_parser.py -q`

Expected: PASS, including current YTD and risk-header cases.

- [ ] **Step 5: Commit**

```bash
git add app/ocr/table_parser.py tests/unit/test_table_parser.py && git commit -m "fix: parse repeated OCR table headers"
```

### Task 2: Add Review-Oriented View Models

**Files:**
- Modify: `app/main.py`
- Test: `tests/e2e/test_lan_flow.py`

**Interfaces:**
- Consumes: `UpdateRun.items`, `METRIC_FIELDS`, and `MISSING_METRIC_STATUSES`
- Produces: preview summary plus `missing_fields`, `recognized_fields`, `recognized_count`, and `missing_count` in `review_row`

- [ ] **Step 1: Write failing UI behavior assertions**

```python
preview = client.get(f"/updates/{run_id}/preview")
assert "待人工审核 1 条" in preview.text
assert "已识别 1 / 12 项" in preview.text
assert "去审核" in preview.text
review = client.get(f"/updates/{run_id}/review")
assert "需补录（11 项）" in review.text
assert "已识别（1 项，可修改）" in review.text
```

- [ ] **Step 2: Verify red**

Run: `/Users/kale/Documents/熊总/.venv/bin/python -m pytest tests/e2e/test_lan_flow.py -k review_summary -q`

Expected: FAIL because current pages expose only raw states and one metric grid.

- [ ] **Step 3: Add display-only helpers**

```python
def recognized_metric_fields(item: RunItem) -> tuple[MetricField, ...]:
    return tuple(field for field in METRIC_FIELDS if field.name in item.metric_values)
```

Build preview counts from `run.items`. Extend `review_row` with ordered missing and recognized fields. Do not change review persistence or validation.

- [ ] **Step 4: Verify green**

Run: `/Users/kale/Documents/熊总/.venv/bin/python -m pytest tests/e2e/test_lan_flow.py -q`

Expected: PASS, including draft-preservation coverage.

- [ ] **Step 5: Commit**

```bash
git add app/main.py tests/e2e/test_lan_flow.py && git commit -m "feat: summarize OCR review work"
```

### Task 3: Productize OCR Workflow Pages

**Files:**
- Modify: `app/templates/new_update.html`
- Modify: `app/templates/preview.html`
- Modify: `app/templates/review.html`
- Modify: `app/static/app.css`
- Test: `tests/e2e/test_lan_flow.py`

**Interfaces:**
- Consumes: Task 2 template context and existing review POST field names.
- Produces: upload date guidance, preview summaries, direct review actions, and grouped metrics.

- [ ] **Step 1: Write failing rendered-page assertions**

```python
assert "同一报告日期" in client.get("/updates/new").text
assert "识别结果" in preview.text
assert "待人工审核 1 条" in preview.text
assert "需补录（11 项）" in review.text
```

- [ ] **Step 2: Verify red**

Run: `/Users/kale/Documents/熊总/.venv/bin/python -m pytest tests/e2e/test_lan_flow.py -k "review_summary or upload_guidance" -q`

Expected: FAIL because the current templates use operational codes and one undifferentiated grid.

- [ ] **Step 3: Update templates and CSS**

```html
<section class="review-metric-group">
  <h3>需补录（{{ row.missing_count }} 项）</h3>
  <div class="review-metrics">{% for field in row.missing_fields %}<label class="metric-field missing">{{ field.label }}<span class="metric-flag">待补</span><input type="text" name="{{ field.name }}" value="{{ row.metric_values[field.name] }}"></label>{% endfor %}</div>
</section>
```

Keep input names, CSRF fields, form actions, and draft values unchanged. Use semantic headings and responsive layout; do not add a frontend framework.

- [ ] **Step 4: Verify green**

Run: `/Users/kale/Documents/熊总/.venv/bin/python -m pytest tests/e2e/test_lan_flow.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/templates/new_update.html app/templates/preview.html app/templates/review.html app/static/app.css tests/e2e/test_lan_flow.py && git commit -m "feat: clarify OCR review workflow"
```

### Task 4: Document, Verify, And Deploy

**Files:**
- Modify: `README.md`
- Test: full suite, ruff, and one single-date Unraid report.

**Interfaces:**
- Consumes: completed parser and workflow changes.
- Produces: documented batch-date rule and validated Docker deployment.

- [ ] **Step 1: Add usage guidance**

```markdown
同一批次中的截图必须来自同一个报告日期；不同周度或历史报告请分别新建批次。
```

- [ ] **Step 2: Run quality checks**

Run: `/Users/kale/Documents/熊总/.venv/bin/python -m pytest -q`

Expected: PASS.

Run: `/Users/kale/Documents/熊总/.venv/bin/ruff check .`

Expected: `All checks passed!`

- [ ] **Step 3: Commit and deploy**

```bash
git add README.md && git commit -m "docs: clarify screenshot batch dates"
```

Deploy with Docker Compose on `192.168.5.28`, submit one single-date report, and verify the preview summary, grouped review fields, output download, and health endpoint.
