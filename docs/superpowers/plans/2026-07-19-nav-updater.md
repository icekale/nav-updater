# 投研净值更新工具 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Docker Compose LAN application that accepts the existing net-value workbook and screenshots, extracts or fetches data, calculates agreed metrics, produces a new workbook, and preserves every processing run for multiple authenticated users.

**Architecture:** A FastAPI web container handles authentication, uploads, previews, confirmations, and downloads. A separate worker container claims durable jobs from PostgreSQL and performs OCR, provider calls, metric calculations, and XML-level XLSX updates. PostgreSQL stores the catalog, observations, runs, per-field statuses, and audit events; Docker volumes store input/output files.

**Tech Stack:** Python 3.12, FastAPI, Jinja2/HTMX, SQLAlchemy 2, Alembic, PostgreSQL 16, RapidOCR with ONNX Runtime, Pillow/OpenCV, lxml, pytest, Playwright, Docker Compose.

---

## File Map

- Create: `pyproject.toml` — dependencies, Ruff, pytest, and package metadata.
- Create: `Dockerfile` — one application image used by both `app` and `worker` services.
- Create: `docker-compose.yml` — `app`, `worker`, and `db` services plus persistent volumes and health checks.
- Create: `.env.example` — non-secret runtime settings and initial admin variables.
- Create: `app/main.py` — FastAPI application factory and route registration.
- Create: `app/config.py` — typed environment settings.
- Create: `app/db.py` — SQLAlchemy engine, session dependency, and transaction helpers.
- Create: `app/models.py` — catalog, run, file, observation, audit, and user models.
- Create: `app/auth.py` — password hashing, session cookies, role guards, and CSRF helpers.
- Create: `app/domain/metrics.py` — pure date-window, return, Sharpe, and drawdown calculations.
- Create: `app/domain/matching.py` — code/name normalization and non-fuzzy matching.
- Create: `app/domain/types.py` — typed source, row, and metric status values.
- Create: `app/providers/public_fund.py` — Eastmoney provider adapter and response normalization.
- Create: `app/ocr/engine.py` — RapidOCR wrapper and normalized OCR token model.
- Create: `app/ocr/table_parser.py` — coordinate-based table reconstruction and numeric parsing.
- Create: `app/excel/template_adapter.py` — fixed workbook schema inspection and XML-level cell updates.
- Create: `app/jobs/service.py` — durable run creation, preview, confirmation, and job state transitions.
- Create: `app/jobs/worker.py` — PostgreSQL job claim loop, heartbeat, retry, and recovery.
- Create: `app/templates/*.html` — login, upload, preview, result, history, catalog, and admin pages.
- Create: `app/static/app.css` — compact internal-tool styling.
- Create: `alembic.ini`, `migrations/env.py`, `migrations/versions/0001_initial.py` — database migration.
- Create: `tests/unit/test_metrics.py` — deterministic metric tests.
- Create: `tests/unit/test_matching.py` — matching and catalog import tests.
- Create: `tests/unit/test_table_parser.py` — OCR token and numeric parsing tests.
- Create: `tests/unit/test_excel_adapter.py` — workbook preservation and cell update tests.
- Create: `tests/integration/test_provider.py` — mocked public provider tests.
- Create: `tests/integration/test_jobs.py` — database-backed run state tests.
- Create: `tests/e2e/test_lan_flow.py` — browser flow against the running app.
- Create: `tests/fixtures/` — small generated XLSX, OCR token JSON, and provider responses.
- Create: `README.md` — Docker deployment, first admin setup, backup, restore, and user workflow.

## Task 1: Scaffold the Python package and Docker baseline

**Files:** `pyproject.toml`, `Dockerfile`, `docker-compose.yml`, `.env.example`, `app/config.py`, `app/main.py`, `tests/test_health.py`

- [ ] **Step 1: Write the health endpoint test.**

```python
def test_health_returns_ok(test_client):
    response = test_client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
```

- [ ] **Step 2: Run the focused test and verify it fails.**

Run: `pytest tests/test_health.py -q`

Expected: FAIL because the package and `/healthz` route do not exist.

- [ ] **Step 3: Add the minimal FastAPI app and package metadata.**

`app/main.py` must expose `create_app()` and add `GET /healthz`; `pyproject.toml` must define the `pytest` test path and runtime dependencies. Keep the first app importable without a database connection so unit tests can run locally.

- [ ] **Step 4: Add Compose services.**

`docker-compose.yml` must define `db` with a PostgreSQL volume, `app` on port `8080`, and `worker` using the same image with `python -m app.jobs.worker`. Add a database health check and make `app`/`worker` depend on it. Do not expose PostgreSQL to the LAN.

- [ ] **Step 5: Run the test and Docker smoke check.**

Run: `pytest tests/test_health.py -q` and `docker compose config`

Expected: the test passes and Compose prints a valid configuration.

- [ ] **Step 6: Commit.**

```bash
git add pyproject.toml Dockerfile docker-compose.yml .env.example app tests/test_health.py
git commit -m "feat: scaffold LAN application"
```

## Task 2: Implement pure metric calculations first

**Files:** `app/domain/metrics.py`, `app/domain/types.py`, `tests/unit/test_metrics.py`

- [ ] **Step 1: Write failing tests for the fixed metric rules.**

Cover: latest Friday cutoff in `Asia/Shanghai`, previous-Friday weekly return, prior-month-end MTD, prior-year-end YTD, completed-year return, 14-day public staleness, 45-day private staleness, sample-standard-deviation Sharpe with zero risk-free rate, and maximum drawdown. Use a fixed list of dated cumulative NAV observations and assert exact unrounded `Decimal` results.

```python
def test_mtd_uses_previous_month_end(nav_series):
    result = calculate_returns(nav_series, cutoff=date(2026, 7, 17))
    assert result.mtd == Decimal("0.10")

def test_stale_public_nav_is_not_used(nav_series):
    result = calculate_returns(nav_series, cutoff=date(2026, 7, 17), kind="public")
    assert result.status["ytd"] == MetricStatus.STALE
```

- [ ] **Step 2: Run the focused tests and verify they fail.**

Run: `pytest tests/unit/test_metrics.py -q`

Expected: FAIL because `calculate_returns`, `calculate_sharpe`, and `calculate_max_drawdown` are undefined.

- [ ] **Step 3: Implement typed, side-effect-free calculations.**

Use `Decimal` for values, timezone-aware cutoff handling, explicit `MetricResult` values/statuses, `ddof=1` for Sharpe, and a preceding observation as the first return baseline. Never silently backfill beyond the configured stale threshold.

- [ ] **Step 4: Run the focused tests and add boundary cases.**

Run: `pytest tests/unit/test_metrics.py -q`

Expected: all metric tests pass, including insufficient observations, duplicate dates, non-positive NAV, and zero standard deviation.

- [ ] **Step 5: Commit.**

```bash
git add app/domain tests/unit/test_metrics.py
git commit -m "feat: add NAV metric calculations"
```

## Task 3: Add database models, migrations, and catalog import

**Files:** `app/db.py`, `app/models.py`, `app/domain/matching.py`, `alembic.ini`, `migrations/env.py`, `migrations/versions/0001_initial.py`, `tests/unit/test_matching.py`, `tests/integration/test_jobs.py`

- [ ] **Step 1: Write failing catalog and state-transition tests.**

Test three-column CSV/XLSX catalog import, duplicate code rejection, product type validation, whitespace/full-width normalization, exact code/name matching, and durable run transitions from `uploaded` to `needs_review` to `completed_with_warnings`.

```python
def test_catalog_rejects_duplicate_product_codes(catalog_import):
    with pytest.raises(CatalogConflict):
        catalog_import("product_name,product_code,product_type\nA,001856,public\nB,001856,public\n")
```

- [ ] **Step 2: Run focused tests and verify they fail.**

Run: `pytest tests/unit/test_matching.py tests/integration/test_jobs.py -q`

Expected: FAIL because models, import service, and job transitions do not exist.

- [ ] **Step 3: Implement SQLAlchemy models and initial Alembic migration.**

Create `users`, `products`, `nav_observations`, `update_runs`, `run_files`, `run_items`, and `audit_logs`. Store separate `match_source`, `row_status`, and per-metric status JSON/columns. Add unique constraints for product code, product/date/source observations, and active catalog entries.

- [ ] **Step 4: Implement catalog import and matching.**

Accept `.csv` and `.xlsx` with exact columns `product_name`, `product_code`, `product_type`; normalize only whitespace and full-width punctuation; reject fuzzy matches and conflicts. Return a row-level error report without partially activating a conflicting catalog.

- [ ] **Step 5: Run migrations and tests.**

Run: `alembic upgrade head` and `pytest tests/unit/test_matching.py tests/integration/test_jobs.py -q`

Expected: migration succeeds and all focused tests pass.

- [ ] **Step 6: Commit.**

```bash
git add app/db.py app/models.py app/domain/matching.py alembic.ini migrations tests
git commit -m "feat: add catalog and durable run storage"
```

## Task 4: Implement OCR parsing and public fund provider adapters

**Files:** `app/ocr/engine.py`, `app/ocr/table_parser.py`, `app/providers/public_fund.py`, `tests/unit/test_table_parser.py`, `tests/integration/test_provider.py`, `tests/fixtures/ocr_tokens.json`, `tests/fixtures/eastmoney_response.json`

- [ ] **Step 1: Write parser and provider tests against fixed fixtures.**

Test coordinate grouping into rows, product/name/value extraction, percent/commas/parentheses normalization, ambiguous values becoming `needs_review`, exact public-fund response normalization, timeout handling, and malformed response handling.

```python
def test_parenthesized_percent_is_negative():
    assert parse_percent("(1.25%)") == Decimal("-0.0125")
```

- [ ] **Step 2: Run focused tests and verify they fail.**

Run: `pytest tests/unit/test_table_parser.py tests/integration/test_provider.py -q`

Expected: FAIL because the OCR and provider adapters do not exist.

- [ ] **Step 3: Implement the OCR adapter and coordinate parser.**

Wrap RapidOCR so the rest of the application receives a stable token structure containing text, bounding box, and confidence. Implement preprocessing with Pillow/OpenCV, coordinate row grouping, confidence thresholds, and the numeric normalization rules from the design.

- [ ] **Step 4: Implement the Eastmoney adapter.**

Use a single provider interface returning dated cumulative NAV observations and source metadata. Set request timeouts, identify non-2xx/invalid JSON responses, and raise typed provider errors so one failed product does not fail the batch.

- [ ] **Step 5: Run focused tests and commit.**

Run: `pytest tests/unit/test_table_parser.py tests/integration/test_provider.py -q`

Expected: all parser/provider tests pass without network access by using the checked-in fixture response.

```bash
git add app/ocr app/providers tests/fixtures tests/unit/test_table_parser.py tests/integration/test_provider.py
git commit -m "feat: add OCR and public fund adapters"
```

## Task 5: Build the template-preserving XLSX adapter

**Files:** `app/excel/template_adapter.py`, `tests/fixtures/net_value_template.xlsx`, `tests/unit/test_excel_adapter.py`

- [ ] **Step 1: Add a minimal fixture copied from the supplied template.**

Keep the real workbook structure needed for testing: merged A-C category cells, product names in E, target columns F-Q, and blank product-name rows. Do not generate a simplified replacement that omits the package parts the adapter must preserve.

- [ ] **Step 2: Write failing adapter tests.**

Assert: blank product rows remain byte-equivalent in their target cells; named rows receive percent-point values; stale cells get the error style; worksheet merges, dimensions, freeze panes, conditional-format XML, and all non-target ZIP members remain unchanged; output reopens in Excel-compatible parsers.

- [ ] **Step 3: Run focused tests and verify they fail.**

Run: `pytest tests/unit/test_excel_adapter.py -q`

Expected: FAIL because the XML adapter does not exist.

- [ ] **Step 4: Implement XML-level updates.**

Use `zipfile` and `lxml.etree` to edit only the target worksheet cells and append one red-error cell style. Preserve all unrelated ZIP members and existing XML extensions. Map columns by the fixed template header values, not hard-coded row numbers; retain original cell values for stale/failed metrics and add the error style only to those cells.

- [ ] **Step 5: Run tests and commit.**

Run: `pytest tests/unit/test_excel_adapter.py -q`

Expected: all preservation and value tests pass.

```bash
git add app/excel tests/fixtures/net_value_template.xlsx tests/unit/test_excel_adapter.py
git commit -m "feat: preserve and update NAV workbook template"
```

## Task 6: Implement durable jobs and end-to-end update orchestration

**Files:** `app/jobs/service.py`, `app/jobs/worker.py`, `tests/integration/test_jobs.py`

- [ ] **Step 1: Write failing orchestration tests.**

Cover upload metadata, cutoff freezing, source priority (explicit screenshot metric before calculated value), preview actions (edit, rematch, skip), public fallback by code, stale/failed retention, output registration, and worker recovery after a stale heartbeat.

- [ ] **Step 2: Run focused tests and verify they fail.**

Run: `pytest tests/integration/test_jobs.py -q`

Expected: FAIL because job creation, preview, confirmation, and worker claim functions do not exist.

- [ ] **Step 3: Implement run creation and preview.**

On upload, store input files and SHA-256 metadata, freeze `cutoff_date`, create `run_items` for nonblank product rows, and enqueue one `update_run`. Preview must expose the current OCR value, confidence, proposed product, source, and action choices.

- [ ] **Step 4: Implement worker claim and heartbeat.**

Use a PostgreSQL transaction to claim one queued run, update heartbeat at least every 10 seconds, process rows independently, and persist per-field statuses. Use `needs_review` when OCR or matching is ambiguous; unresolved rows retain original values and are marked stale/failed in the output.

- [ ] **Step 5: Implement output commit and recovery.**

Write the new workbook to a temporary path, reopen and validate it, atomically move it into the output volume, then commit output metadata and final run status. On startup, requeue runs with a heartbeat older than 30 minutes and no output registration.

- [ ] **Step 6: Run integration tests and commit.**

Run: `pytest tests/integration/test_jobs.py -q`

Expected: all orchestration and recovery tests pass.

```bash
git add app/jobs tests/integration/test_jobs.py
git commit -m "feat: add durable NAV update jobs"
```

## Task 7: Add authentication, LAN pages, and audit actions

**Files:** `app/auth.py`, `app/main.py`, `app/templates/*.html`, `app/static/app.css`, `tests/e2e/test_lan_flow.py`

- [ ] **Step 1: Write the browser acceptance test.**

The test creates an admin and normal user, logs in, imports the catalog as admin, uploads fixture files as normal user, resolves a preview row, downloads the generated workbook, and verifies the history entry and audit records.

- [ ] **Step 2: Run the browser test and verify it fails.**

Run: `pytest tests/e2e/test_lan_flow.py -q`

Expected: FAIL because login, templates, and routes do not exist.

- [ ] **Step 3: Implement authentication and role guards.**

Hash passwords with Argon2id, use server-side sessions with HttpOnly/SameSite cookies, add CSRF tokens to all state-changing forms, and provide admin-only catalog/account routes. Do not add self-registration.

- [ ] **Step 4: Implement the internal pages.**

Build compact server-rendered pages for login, new update, preview/resolve, results, history, catalog import, and account management. Use familiar text controls; display each row’s source, status, error reason, and exact download links. Keep the first UI functional rather than adding charts or a dashboard.

- [ ] **Step 5: Run the browser flow and commit.**

Run: `pytest tests/e2e/test_lan_flow.py -q`

Expected: the complete upload-to-download flow passes and all actions create audit records.

```bash
git add app/auth.py app/main.py app/templates app/static tests/e2e/test_lan_flow.py
git commit -m "feat: add authenticated LAN workflow"
```

## Task 8: Finish Docker deployment, backups, and verification

**Files:** `README.md`, `docker-compose.yml`, `scripts/backup.sh`, `scripts/restore.sh`, `tests/e2e/test_lan_flow.py`

- [ ] **Step 1: Add deployment and backup commands.**

Document `.env` creation, `docker compose up -d --build`, initial admin login, LAN URL discovery, logs, health checks, PostgreSQL dump, file-volume archive, restore order, and shutdown. Scripts must fail on missing variables and never print secrets.

- [ ] **Step 2: Add restart and persistence checks.**

Extend the E2E test to stop/start the Compose services and verify users, catalog, run history, and generated files remain accessible.

- [ ] **Step 3: Run the complete verification suite.**

Run:

```bash
pytest -q
docker compose config
docker compose up -d --build
pytest tests/e2e/test_lan_flow.py -q
docker compose down
```

Expected: all tests pass, Compose starts `app`, `worker`, and `db`, the browser flow works from the LAN-bound port, and shutdown leaves volumes intact.

- [ ] **Step 4: Commit the deployable MVP.**

```bash
git add README.md docker-compose.yml scripts tests
git commit -m "chore: document and verify Docker deployment"
```

## Self-Review Checklist

- [ ] Design sections map to Tasks 1-8: Docker, auth, OCR, provider, catalog, metrics, Excel preservation, jobs, history, audit, and deployment are covered.
- [ ] No meeting-workbench or WeChat implementation is included in this plan.
- [ ] All metric formulas use the same cutoff, staleness, precision, and status rules as the design.
- [ ] Screenshot values, calculated values, and public provider values have explicit source priority.
- [ ] The XLSX adapter preserves non-target package parts and has a fixture-based byte-comparison test.
- [ ] The worker is durable and does not block FastAPI requests.
- [ ] Every task has a focused failing test, an implementation step, a passing command, and a commit boundary.
