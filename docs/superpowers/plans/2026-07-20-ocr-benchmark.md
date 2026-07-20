# OCR Benchmark Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Measure OCR product and metric correctness from researcher-confirmed labels without changing any operational data.

**Architecture:** A pure `app.ocr.benchmark` module validates labels, evaluates parser rows, and renders JSON/Markdown data. A thin command-line script loads source images from a caller-selected directory and writes one report directory. Labels are versioned JSON but source images and live database records never enter the repository.

**Tech Stack:** Python 3.12, Decimal, hashlib, argparse, JSON, Markdown, pytest.

---

### Task 1: Add Label And Evaluation Domain Types

**Files:**
- Create: `app/ocr/benchmark.py`
- Create: `tests/unit/test_ocr_benchmark.py`

- [ ] **Step 1: Write failing evaluation tests**

```python
def test_evaluate_cases_counts_numeric_and_confirmed_blank_fields() -> None:
    case = BenchmarkCase("report.png", "a" * 64, "产品A", {
        "weekly": Decimal("0.01"), "mtd": None,
    })
    row = OCRMetricRow("产品A", None, {"weekly": Decimal("0.01")}, 0.99, frozenset({"mtd"}))
    report = evaluate_cases([case], {"report.png": [row]})
    assert report.product_matches == 1
    assert report.correct_fields == 2
    assert report.missed_fields == report.wrong_fields == report.wrong_column_fields == 0
```

```python
def test_evaluate_cases_flags_value_found_in_a_different_metric_column() -> None:
    case = BenchmarkCase("report.png", "a" * 64, "产品A", {
        "weekly": Decimal("0.01"), "mtd": Decimal("0.02"),
    })
    row = OCRMetricRow("产品A", None, {"weekly": Decimal("0.02"), "mtd": Decimal("0.01")}, 0.99)
    report = evaluate_cases([case], {"report.png": [row]})
    assert report.wrong_fields == 2
    assert report.wrong_column_fields == 2
```

- [ ] **Step 2: Verify red**

Run: `/Users/kale/Documents/熊总/.venv/bin/python -m pytest tests/unit/test_ocr_benchmark.py -q`

Expected: FAIL because `app.ocr.benchmark` does not exist.

- [ ] **Step 3: Implement the pure evaluator**

```python
@dataclass(frozen=True)
class BenchmarkCase:
    image: str
    sha256: str
    product_name: str
    metrics: Mapping[str, Decimal | None]

def evaluate_cases(
    cases: Iterable[BenchmarkCase], rows_by_image: Mapping[str, list[OCRMetricRow]]
) -> BenchmarkReport:
    ...
```

Match product names with `normalize_name()`. Score a `None` expectation as correct only when the OCR row marks the field in `blank_metrics`; score absent numeric or blank evidence as missed; detect a wrong column only when the expected decimal occurs in exactly one other parsed metric column.

- [ ] **Step 4: Verify green**

Run: `/Users/kale/Documents/熊总/.venv/bin/python -m pytest tests/unit/test_ocr_benchmark.py -q`

Expected: PASS.

### Task 2: Validate Labels And Render Reports

**Files:**
- Modify: `app/ocr/benchmark.py`
- Modify: `tests/unit/test_ocr_benchmark.py`

- [ ] **Step 1: Write failing IO tests**

```python
def test_load_cases_rejects_incomplete_metrics(tmp_path: Path) -> None:
    labels = tmp_path / "labels.json"
    labels.write_text(json.dumps({"cases": [{"image": "report.png", "sha256": "a" * 64,
        "product_name": "产品A", "metrics": {"weekly": "0.01"}}]}))
    with pytest.raises(BenchmarkFormatError, match="metrics"):
        load_cases(labels)
```

```python
def test_render_markdown_includes_rates_and_wrong_column_count() -> None:
    markdown = render_markdown(report)
    assert "产品匹配率" in markdown
    assert "错列率" in markdown
```

- [ ] **Step 2: Verify red**

Run: `/Users/kale/Documents/熊总/.venv/bin/python -m pytest tests/unit/test_ocr_benchmark.py -k "incomplete or markdown" -q`

Expected: FAIL because label validation and rendering are absent.

- [ ] **Step 3: Implement strict label loading and rendering**

Accept only a JSON object with `version: 1` and `cases`. Require a 64-character lowercase SHA-256, nonblank image and product name, and exactly the 12 metric keys from `app.ocr.table_parser.METRIC_KEYS`; accept decimal strings and JSON null only. Provide `report_as_dict()` and `render_markdown()` with aggregate and per-image counts plus mismatch rows.

- [ ] **Step 4: Verify green**

Run: `/Users/kale/Documents/熊总/.venv/bin/python -m pytest tests/unit/test_ocr_benchmark.py -q`

Expected: PASS.

### Task 3: Add A Read-Only Benchmark Command And Label Template

**Files:**
- Create: `scripts/run_ocr_benchmark.py`
- Create: `benchmarks/ocr/labels.example.json`
- Create: `benchmarks/ocr/README.md`
- Modify: `tests/unit/test_ocr_benchmark.py`

- [ ] **Step 1: Write failing command-path test**

```python
def test_verify_source_hash_rejects_changed_image(tmp_path: Path) -> None:
    image = tmp_path / "report.png"
    image.write_bytes(b"changed")
    case = BenchmarkCase("report.png", "a" * 64, "产品A", all_metrics())
    with pytest.raises(BenchmarkSourceError, match="SHA-256"):
        verify_source_hashes([case], tmp_path)
```

- [ ] **Step 2: Verify red**

Run: `/Users/kale/Documents/熊总/.venv/bin/python -m pytest tests/unit/test_ocr_benchmark.py -k changed_image -q`

Expected: FAIL because source verification is absent.

- [ ] **Step 3: Implement command behavior**

`run_ocr_benchmark.py` accepts `--labels`, `--images-root`, and `--output-dir`. It loads and verifies every image before OCR, recognizes each unique source once, evaluates rows, and writes `summary.md` and `details.json`. Refuse a nonempty output directory. Do not import database modules or write to image paths.

The example file contains one complete twelve-metric schema with placeholder source metadata and the README explains the 30-row sampling rule and how a researcher marks visually unavailable cells as JSON `null`.

- [ ] **Step 4: Verify green**

Run: `/Users/kale/Documents/熊总/.venv/bin/python -m pytest tests/unit/test_ocr_benchmark.py -q`

Expected: PASS.

### Task 4: Full Verification And Commit

**Files:**
- Test: full suite and lint

- [ ] **Step 1: Run all checks**

Run: `/Users/kale/Documents/熊总/.venv/bin/python -m pytest -q`

Expected: PASS.

Run: `/Users/kale/Documents/熊总/.venv/bin/ruff check .`

Expected: `All checks passed!`

- [ ] **Step 2: Commit**

```bash
git add app/ocr/benchmark.py scripts/run_ocr_benchmark.py benchmarks/ocr tests/unit/test_ocr_benchmark.py docs/superpowers/specs/2026-07-20-ocr-benchmark-design.md docs/superpowers/plans/2026-07-20-ocr-benchmark.md
git commit -m "feat: add OCR benchmark runner"
```
