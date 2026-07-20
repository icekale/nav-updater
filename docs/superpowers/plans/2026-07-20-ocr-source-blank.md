# OCR Source Blank Classification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Clear explicitly unavailable performance fields from a report without needlessly requiring manual review, while retaining review for fields OCR did not recognize.

**Architecture:** The table parser keeps confirmed source blanks separate from parsed numeric values. The processor maps confirmed blanks to a non-stale status and explicit `None` Excel updates. The existing adapter uses stale state to distinguish a deliberate clear from a missing value that must be retained and marked red.

**Tech Stack:** Python 3.12, Decimal, lxml, SQLAlchemy, pytest.

---

### Task 1: Represent Confirmed Source Blanks

**Files:**
- Modify: `app/ocr/table_parser.py`
- Test: `tests/unit/test_table_parser.py`

- [ ] **Step 1: Write the failing parser test**

```python
def test_extract_metric_rows_records_explicit_unavailable_metric_marker() -> None:
    rows = extract_metric_rows([
        token("产品名称", 10, 10), token("近一周(%)", 100, 10), token("MTD(%)", 200, 10),
        token("产品A", 10, 50), token("1.00%", 100, 50), token("--", 200, 50),
    ])
    assert rows[0].metrics == {"weekly": Decimal("0.01")}
    assert rows[0].blank_metrics == frozenset({"mtd"})
```

- [ ] **Step 2: Verify red**

Run: `/Users/kale/Documents/熊总/.venv/bin/python -m pytest tests/unit/test_table_parser.py::test_extract_metric_rows_records_explicit_unavailable_metric_marker -q`

Expected: FAIL because `OCRMetricRow` has no `blank_metrics`.

- [ ] **Step 3: Add the minimum parser behavior**

```python
SOURCE_BLANK_MARKERS = {"-", "--", "—", "n/a"}

def _is_source_blank(text: str) -> bool:
    return text.strip().replace(" ", "").lower() in SOURCE_BLANK_MARKERS
```

Add `blank_metrics: frozenset[str] = frozenset()` to `OCRMetricRow`. During metric parsing, add a header key to `blank_metrics` when the associated cell is a source blank, then return rows containing either numeric metrics or confirmed blanks.

- [ ] **Step 4: Verify green**

Run: `/Users/kale/Documents/熊总/.venv/bin/python -m pytest tests/unit/test_table_parser.py -q`

Expected: PASS.

### Task 2: Map Confirmed Blanks To A Non-Review Excel Update

**Files:**
- Modify: `app/jobs/processor.py`
- Test: `tests/integration/test_jobs.py`

- [ ] **Step 1: Write the failing processing test**

```python
assert item.row_status == "ready"
assert item.metric_status["weekly"] == "extracted"
assert item.metric_status["mtd"] == "source_blank"
assert adapter.updates[item.excel_row]["mtd"] is None
assert "mtd" not in adapter.stale[item.excel_row]
```

Use a fake OCR response containing every metric as either a numeric value or `--`, then assert a matched row is ready and the adapter receives `None` for confirmed blanks.

- [ ] **Step 2: Verify red**

Run: `/Users/kale/Documents/熊总/.venv/bin/python -m pytest tests/integration/test_jobs.py -k source_blank -q`

Expected: FAIL because confirmed blanks are treated as stale OCR omissions.

- [ ] **Step 3: Add the minimum processor mapping**

```python
confirmed_blank_metrics = set(image_row.blank_metrics)
missing_metrics = set(ALL_METRICS) - set(image_row.metrics) - confirmed_blank_metrics
statuses = {
    key: "extracted" if key in image_row.metrics
    else "source_blank" if key in confirmed_blank_metrics
    else MetricStatus.STALE.value
    for key in ALL_METRICS
}
updates[item.excel_row] = {
    **image_row.metrics,
    **{key: None for key in confirmed_blank_metrics},
}
```

Leave `missing_metrics` as the stale set and the sole missing-field review reason.

- [ ] **Step 4: Verify green**

Run: `/Users/kale/Documents/熊总/.venv/bin/python -m pytest tests/integration/test_jobs.py -k "source_blank or partial_ocr_metrics" -q`

Expected: PASS.

### Task 3: Clear Explicit Blank Updates In Excel

**Files:**
- Modify: `app/excel/template_adapter.py`
- Test: `tests/unit/test_excel_adapter.py`

- [ ] **Step 1: Write the failing Excel test**

```python
TemplateAdapter().apply_updates(FIXTURE, first_output, {2: {"weekly": Decimal("0.50")}})
TemplateAdapter().apply_updates(first_output, output, {2: {"weekly": None}})
assert sheet.xpath('string(.//x:c[@r="F2"]/x:v)', namespaces=NS) == ""
assert cell.get("s") is None
```

- [ ] **Step 2: Verify red**

Run: `/Users/kale/Documents/熊总/.venv/bin/python -m pytest tests/unit/test_excel_adapter.py -k confirmed_blank -q`

Expected: FAIL because `None` currently leaves an existing numeric value untouched.

- [ ] **Step 3: Add the minimum adapter distinction**

```python
if value is not None:
    _set_numeric(cell, value.quantize(Decimal("0.01")))
elif metric in values and metric not in stale.get(row_number, set()):
    _set_numeric(cell, None)
```

Keep stale `None` updates unchanged so they preserve the existing cell and apply red styling.

- [ ] **Step 4: Verify green**

Run: `/Users/kale/Documents/熊总/.venv/bin/python -m pytest tests/unit/test_excel_adapter.py -q`

Expected: PASS.

### Task 4: Verify The Contract

- [ ] **Step 1: Run regression checks**

Run: `/Users/kale/Documents/熊总/.venv/bin/python -m pytest -q`

Expected: PASS.

Run: `/Users/kale/Documents/熊总/.venv/bin/ruff check .`

Expected: `All checks passed!`

- [ ] **Step 2: Commit**

```bash
git add app/ocr/table_parser.py app/jobs/processor.py app/excel/template_adapter.py tests/unit/test_table_parser.py tests/integration/test_jobs.py tests/unit/test_excel_adapter.py docs/superpowers/specs/2026-07-20-ocr-source-blank-design.md docs/superpowers/plans/2026-07-20-ocr-source-blank.md
git commit -m "fix: clear confirmed OCR source blanks"
```
