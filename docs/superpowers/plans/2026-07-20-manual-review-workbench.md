# 人工审核工作台 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Allow authenticated users to correct an update-run item manually and regenerate an Excel result that preserves that manual decision.

**Architecture:** Add a focused review service that validates product selection and metric input before updating the existing `RunItem` JSON fields. The processor will recognize `manual` items and write their stored values directly to the workbook. FastAPI adds one review page and one save endpoint; no schema migration or separate frontend is introduced.

**Tech Stack:** Python 3.12, FastAPI/Jinja2, SQLAlchemy 2, pytest, Docker Compose.

---

## File Map

- Create: `app/jobs/review.py` — manual metric fields, parsing, validation, formatting and persistence.
- Modify: `app/jobs/processor.py` — preserve manual item values during processing.
- Modify: `app/main.py` — review routes, audit logging and template data preparation.
- Create: `app/templates/review.html` — per-item review forms.
- Modify: `app/templates/preview.html` — review link and regeneration command.
- Modify: `app/static/app.css` — compact review form grid and narrow-screen layout.
- Modify: `tests/integration/test_jobs.py` — review service and manual processor coverage.
- Modify: `tests/e2e/test_lan_flow.py` — authenticated review-to-download flow.

## Task 1: Persist Validated Manual Reviews

**Files:** `app/jobs/review.py`, `tests/integration/test_jobs.py`

- [ ] **Step 1: Write failing review-service tests.**

```python
def test_save_manual_review_converts_percentages_and_marks_missing_values_stale(session):
    item, product = make_run_item_and_product(session)
    reviewed = save_manual_review(
        session,
        item=item,
        product=product,
        inputs={"weekly": "12.34", "sharpe": "1.25"},
        note="以管理人 7 月 17 日净值表为准",
    )
    assert reviewed.match_source == "manual"
    assert reviewed.metric_values == {"weekly": "0.1234", "sharpe": "1.25"}
    assert reviewed.metric_status["weekly"] == "manual"
    assert reviewed.metric_status["mtd"] == "stale"
```

- [ ] **Step 2: Run the focused test and verify it fails.**

Run: `pytest tests/integration/test_jobs.py::test_save_manual_review_converts_percentages_and_marks_missing_values_stale -q`

Expected: FAIL because `app.jobs.review` and `save_manual_review` do not exist.

- [ ] **Step 3: Implement review parsing and persistence.**

Create `METRIC_FIELDS` with the twelve workbook metrics, Chinese labels and percentage flags. Implement `parse_manual_metrics()` to trim whitespace, accept optional percent signs, convert percentage input to decimal storage values, reject invalid input and require at least one metric. Implement `save_manual_review()` to set `product_id`, `manual` source, manual/stale statuses, an explicit reviewer note and a `ready`/`stale` row status.

- [ ] **Step 4: Run focused tests and commit.**

Run: `pytest tests/integration/test_jobs.py -q`

Expected: all job tests pass.

```bash
git add app/jobs/review.py tests/integration/test_jobs.py
git commit -m "feat: add manual review persistence"
```

## Task 2: Give Manual Data Processing Priority

**Files:** `app/jobs/processor.py`, `tests/integration/test_jobs.py`

- [ ] **Step 1: Write a failing manual-priority processor test.**

```python
def test_process_run_uses_manual_values_without_calling_provider(session, workbook):
    run, item = make_manual_run(session, workbook, {"weekly": "0.1234"})
    adapter = CapturingAdapter()
    process_run(session, run.id, provider=FailingProvider(), ocr_service=object(), adapter=adapter)
    assert adapter.updates[item.excel_row]["weekly"] == Decimal("0.1234")
    assert "mtd" in adapter.stale[item.excel_row]
```

- [ ] **Step 2: Run the focused test and verify it fails.**

Run: `pytest tests/integration/test_jobs.py::test_process_run_uses_manual_values_without_calling_provider -q`

Expected: FAIL because the current processor fetches external data or does not pass stored manual values to the adapter.

- [ ] **Step 3: Implement manual-item handling before OCR/provider resolution.**

In `process_run()`, detect `item.match_source == "manual"`, deserialize stored decimal strings for known metric names, compute stale fields from saved statuses, pass the values to the template adapter and continue without calling OCR or the public provider.

- [ ] **Step 4: Run focused tests and commit.**

Run: `pytest tests/integration/test_jobs.py -q`

Expected: all job tests pass and manual values win over external data.

```bash
git add app/jobs/processor.py tests/integration/test_jobs.py
git commit -m "feat: preserve manual review values during processing"
```

## Task 3: Add the Review Workbench UI

**Files:** `app/main.py`, `app/templates/review.html`, `app/templates/preview.html`, `app/static/app.css`, `tests/e2e/test_lan_flow.py`

- [ ] **Step 1: Write failing browser-flow coverage.**

```python
review = client.get(f"/updates/{run_id}/review")
assert review.status_code == 200
assert "人工审核" in review.text

saved = client.post(
    f"/updates/{run_id}/items/{item_id}/review",
    data={"token": token, "product_id": product_id, "weekly": "12.34", "review_note": "人工核对"},
    follow_redirects=False,
)
assert saved.status_code == 303
assert "manual" in client.get(f"/updates/{run_id}/preview").text
```

- [ ] **Step 2: Run the focused test and verify it fails.**

Run: `pytest tests/e2e/test_lan_flow.py::test_login_catalog_upload_process_and_download -q`

Expected: FAIL because the review page and save endpoint do not exist.

- [ ] **Step 3: Add review routes and audit records.**

Add `GET /updates/{run_id}/review` to load active products and formatted stored metrics. Add `POST /updates/{run_id}/items/{item_id}/review` to validate CSRF, parse fields from the form, call the review service and write an `AuditLog(action="manual_review")` with the product code, edited metrics and note.

- [ ] **Step 4: Add server-rendered forms and navigation.**

Create one form per run item with a product select, a responsive metric grid, a required note and a save command. Add a visible “人工审核” link in preview and allow completed batches to use “重新生成结果”. Keep the existing preview table and download path intact.

- [ ] **Step 5: Run focused tests and commit.**

Run: `pytest tests/e2e/test_lan_flow.py -q`

Expected: the authenticated user can save a manual review, reprocess the batch and download an Excel result.

```bash
git add app/main.py app/templates/review.html app/templates/preview.html app/static/app.css tests/e2e/test_lan_flow.py
git commit -m "feat: add manual review workbench"
```

## Task 4: Full Verification and Deployment

**Files:** `README.md`

- [ ] **Step 1: Document the manual-review workflow.**

Add the human-review and regeneration steps to the usage section, including the rule that blank manual fields retain previous Excel values and become red.

- [ ] **Step 2: Run complete verification.**

Run:

```bash
pytest -q
ruff check .
docker compose config
docker compose up -d --build
curl -fsS http://127.0.0.1:8080/healthz
```

Expected: tests and lint pass, Compose starts all services and health returns `{"status":"ok"}`.

- [ ] **Step 3: Commit.**

```bash
git add README.md
git commit -m "docs: explain manual review workflow"
```

## Self-Review Checklist

- [ ] No database migration is introduced because existing `RunItem` fields hold manual state.
- [ ] Manual values bypass OCR and public providers during regeneration.
- [ ] Blank manual fields retain source workbook values and receive stale styling.
- [ ] All save actions retain CSRF checks and create audit records.
- [ ] Soybean Admin is not added to the deployable image or runtime.
