# 资本市场核心会议工作台 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Import the `近期会议更新` worksheet into a searchable internal meeting library and let authenticated teammates maintain a research record for every meeting.

**Architecture:** A focused `app/meetings.py` module will parse a complete XLSX workbook before database changes and upsert source fields by a stable key. A new `Meeting` model keeps imported source content separate from editable team records. FastAPI renders one searchable list and one detail form, retaining the existing server-rendered session and CSRF model.

**Tech Stack:** Python 3.12, FastAPI, Jinja2, SQLAlchemy 2, Alembic, openpyxl, pytest, Docker Compose.

---

## File Map

- Create: `app/meetings.py` - date parsing, workbook validation, source-key construction and import upsert service.
- Modify: `app/models.py` - `Meeting` SQLAlchemy model.
- Create: `migrations/versions/0002_add_meetings.py` - persistent PostgreSQL/SQLite schema change.
- Modify: `app/main.py` - authenticated meeting list/detail/import/record routes and audit logging.
- Modify: `app/templates/base.html` - meeting navigation link.
- Create: `app/templates/meetings.html` - filters, result table, admin import panel and empty states.
- Create: `app/templates/meeting_detail.html` - read-only source material and editable team record.
- Modify: `app/static/app.css` - compact filter, two-column detail and mobile table behavior using existing tokens.
- Modify: `README.md` - meeting workflow and role rules.
- Create: `tests/unit/test_meetings.py` - pure date/key/workbook validation behavior.
- Create: `tests/integration/test_meeting_import.py` - SQLite import/upsert/manual-field retention.
- Modify: `tests/e2e/test_lan_flow.py` - login, role authorization, import, filtering and record-saving flow.

## Task 1: Define and Migrate Meeting Storage

**Files:** `app/models.py`, `migrations/versions/0002_add_meetings.py`, `tests/integration/test_meeting_import.py`

- [ ] **Step 1: Write the failing model-persistence test.**

```python
def test_meeting_persists_source_and_team_fields() -> None:
    session = sqlite_session()
    meeting = Meeting(
        source_key="a" * 64,
        title="2026陆家嘴论坛",
        date_raw="2026-06-17至2026-06-18",
        date_start=date(2026, 6, 17),
        date_end=date(2026, 6, 18),
        date_parse_status="normalized",
        level="金融高层论坛",
        core_statement="服务高质量发展",
        market_impact="投融资综合改革",
        research_mapping="科技成长",
        follow_up="跟踪改革细则",
        source_link="https://example.test/source",
        source_updated_at="2026-07-18",
        company_tags="券商, 创投",
        industry_tags="金融, 科技",
        attendance_status="planned",
        minutes="安排参会",
        todo="跟踪规则",
        conclusion="长期利好",
    )
    session.add(meeting)
    session.commit()
    assert session.scalar(select(Meeting)).attendance_status == "planned"
```

- [ ] **Step 2: Run it and verify it fails.**

Run: `pytest tests/integration/test_meeting_import.py::test_meeting_persists_source_and_team_fields -q`

Expected: FAIL because `Meeting` is not defined.

- [ ] **Step 3: Add the minimal model and migration.**

Add `Meeting` with a unique/indexed `source_key`, indexed nullable `date_start` and `date_end`, all nine required source columns, `date_parse_status`, six editable team columns, and `imported_at`/updated timestamps. Use `String` for bounded labels and `Text` for long source/team fields. Create matching indexes in migration `0002_add_meetings.py`, with `down_revision = "0001_initial"`.

- [ ] **Step 4: Verify storage and commit.**

Run:

```bash
pytest tests/integration/test_meeting_import.py::test_meeting_persists_source_and_team_fields -q
alembic upgrade head
alembic downgrade 0001_initial
```

Expected: the focused test passes and both Alembic commands exit 0.

```bash
git add app/models.py migrations/versions/0002_add_meetings.py tests/integration/test_meeting_import.py
git commit -m "feat: add meeting storage"
```

## Task 2: Parse and Upsert the Approved Worksheet

**Files:** `app/meetings.py`, `tests/unit/test_meetings.py`, `tests/integration/test_meeting_import.py`

- [ ] **Step 1: Write failing date, key and workbook validation tests.**

```python
def test_parse_date_range_supports_single_date_and_chinese_range() -> None:
    assert parse_date_range("2026-06-06") == (date(2026, 6, 6), date(2026, 6, 6))
    assert parse_date_range("2026-06-17至2026-06-18") == (
        date(2026, 6, 17), date(2026, 6, 18)
    )
    assert parse_date_range("待定") == (None, None)

def test_source_key_normalizes_title_whitespace_without_merging_dates() -> None:
    assert source_key(" 2026 陆家嘴论坛 ", "2026-06-17") == source_key(
        "2026陆家嘴论坛", "2026-06-17"
    )
    assert source_key("2026陆家嘴论坛", "2026-06-17") != source_key(
        "2026陆家嘴论坛", "2026-06-18"
    )

def test_read_meeting_rows_rejects_missing_required_header(tmp_path: Path) -> None:
    workbook = workbook_with_headers(tmp_path, ["会议/事件", "日期"])
    with pytest.raises(MeetingImportError, match="缺少列"):
        read_meeting_rows(workbook)
```

- [ ] **Step 2: Run parser tests and verify they fail.**

Run: `pytest tests/unit/test_meetings.py -q`

Expected: FAIL because `app.meetings` does not exist.

- [ ] **Step 3: Implement pure parsing before persistence.**

Create `REQUIRED_HEADERS` in the exact nine-column order. `read_meeting_rows(path)` must open only the `近期会议更新` sheet, read row 2 headers, coerce source cells to stripped text, skip blank meeting names, and return records only after validating every header. `parse_date_range()` accepts `date`, `datetime`, a single ISO date, and `YYYY-MM-DD至YYYY-MM-DD`; all other values produce `(None, None)`. `source_key()` hashes whitespace-normalized title plus original date with SHA-256.

- [ ] **Step 4: Write the failing import/upsert test.**

```python
def test_import_updates_source_but_preserves_team_record(tmp_path: Path) -> None:
    session = sqlite_session()
    first = import_meetings(session, meeting_workbook(tmp_path, impact="首次影响"))
    assert (first.created, first.updated) == (1, 0)
    meeting = session.scalar(select(Meeting))
    meeting.company_tags = "券商"
    meeting.minutes = "研究员会议纪要"
    session.commit()

    second = import_meetings(session, meeting_workbook(tmp_path, impact="更新影响"))
    refreshed = session.scalar(select(Meeting))
    assert (second.created, second.updated) == (0, 1)
    assert refreshed.market_impact == "更新影响"
    assert refreshed.company_tags == "券商"
    assert refreshed.minutes == "研究员会议纪要"
```

- [ ] **Step 5: Run it and verify it fails.**

Run: `pytest tests/integration/test_meeting_import.py::test_import_updates_source_but_preserves_team_record -q`

Expected: FAIL because `import_meetings()` does not exist.

- [ ] **Step 6: Implement transactional upsert.**

`import_meetings(session, path)` first calls `read_meeting_rows()`. It queries each stable key, creates new records with default team fields, or updates only source/date/import timestamp fields. It returns `ImportResult(created, updated, skipped)` and never calls `commit()`; the HTTP route owns the transaction and audit entry.

- [ ] **Step 7: Run all import coverage and commit.**

Run: `pytest tests/unit/test_meetings.py tests/integration/test_meeting_import.py -q`

Expected: parser, validation, first import, repeat import and field-retention tests pass.

```bash
git add app/meetings.py tests/unit/test_meetings.py tests/integration/test_meeting_import.py
git commit -m "feat: import meeting workbooks"
```

## Task 3: Build the Authenticated Meeting Workbench

**Files:** `app/main.py`, `app/templates/base.html`, `app/templates/meetings.html`, `app/templates/meeting_detail.html`, `app/static/app.css`, `tests/e2e/test_lan_flow.py`

- [ ] **Step 1: Write a failing administrator import and team-record flow.**

```python
meetings = client.get("/meetings")
uploaded = client.post(
    "/meetings/import",
    data={"token": csrf_from(meetings.text)},
    files={"workbook": ("meetings.xlsx", meeting_xlsx, XLSX_MEDIA_TYPE)},
    follow_redirects=False,
)
assert uploaded.status_code == 303
assert "2026陆家嘴论坛" in client.get("/meetings").text

detail = client.get("/meetings/1")
saved = client.post(
    "/meetings/1/record",
    data={
        "token": csrf_from(detail.text),
        "company_tags": "券商, 创投",
        "industry_tags": "金融",
        "attendance_status": "attended",
        "minutes": "已参会，关注投融资改革",
        "todo": "跟踪细则",
        "conclusion": "长期利好",
    },
    follow_redirects=False,
)
assert saved.status_code == 303
assert "已参会" in client.get("/meetings/1").text
```

- [ ] **Step 2: Run it and verify it fails.**

Run: `pytest tests/e2e/test_lan_flow.py::test_admin_imports_and_user_updates_meeting_record -q`

Expected: FAIL because `/meetings` does not exist.

- [ ] **Step 3: Add routes with explicit authorization and audit records.**

Add `GET /meetings` for authenticated users. It accepts `q`, `date_from`, `date_to`, `level`, `company`, and `industry`; date filtering includes normalized ranges overlapping the requested interval. Add `POST /meetings/import` guarded by `require_admin`, CSRF and an `.xlsx` suffix check. Parse errors re-render the list without committing; success writes `AuditLog(action="import", object_type="meeting_workbook")` with filename and counts. Add `GET /meetings/{meeting_id}` and CSRF-protected `POST /meetings/{meeting_id}/record`; only accept `unplanned`, `planned`, `attended`, or `absent`, then write `AuditLog(action="update", object_type="meeting")` with changed field names.

- [ ] **Step 4: Add list/detail templates and responsive CSS.**

Add a “会议跟踪” navigation link. The list uses a compact filter form, shows an import form only to administrators, gives a clear empty state, and lists date, meeting, level, impact summary, tags, attendance and detail link. The detail page separates read-only “来源内容” and editable “团队记录”; source links must open separately with `rel="noreferrer"`. Preserve existing navigation and update workflows. Use the established blue/gray palette, 4–6px controls and system sans. At narrow widths, the table scrolls horizontally rather than compressing long text.

- [ ] **Step 5: Add filtering, authorization and audit assertions.**

```python
filtered = client.get("/meetings?company=%E5%88%B8%E5%95%86&date_from=2026-06-01&date_to=2026-06-30")
assert "2026陆家嘴论坛" in filtered.text
assert session.query(AuditLog).filter_by(action="import", object_type="meeting_workbook").count() == 1
assert session.query(AuditLog).filter_by(action="update", object_type="meeting").count() == 1

with TestClient(user_app) as user_client:
    response = user_client.post("/meetings/import", data={"token": valid_token})
    assert response.status_code == 403
```

- [ ] **Step 6: Run page-flow tests and commit.**

Run: `pytest tests/e2e/test_lan_flow.py -q`

Expected: existing update workflow remains green; administrators can import; regular users can maintain team records but receive 403 for imports.

```bash
git add app/main.py app/templates/base.html app/templates/meetings.html app/templates/meeting_detail.html app/static/app.css tests/e2e/test_lan_flow.py
git commit -m "feat: add meeting workbench"
```

## Task 4: Document, Import and Verify the Deployable Service

**Files:** `README.md`

- [ ] **Step 1: Update the usage manual.**

Replace the meeting-workbench entry under “当前边界” with a “会议跟踪” section. Document the accepted `近期会议更新` sheet, its nine required headers, duplicate-import behavior, user/admin roles, tag syntax and the fact that other comparison worksheets are not automatically imported.

- [ ] **Step 2: Import the real workbook after deployment.**

Run:

```bash
docker compose up -d --build
docker compose exec app alembic upgrade head
curl -fsS http://127.0.0.1:8080/healthz
```

Log in as administrator, upload `/Volumes/main/codex-temp-share/资本市场核心会议跟踪.xlsx`, and verify the three source records from `近期会议更新`.

- [ ] **Step 3: Run complete verification.**

Run:

```bash
pytest -q
ruff check .
docker compose config
```

Expected: all tests and Ruff pass and Compose renders a valid configuration.

- [ ] **Step 4: Commit documentation.**

```bash
git add README.md
git commit -m "docs: document meeting workbench"
```

## Self-Review Checklist

- [ ] Only `近期会议更新` is structured-imported; no comparison worksheet is written to the database.
- [ ] Every source import validates the entire workbook before database mutation and preserves existing team fields on updates.
- [ ] Imports remain admin-only; all logged-in users can maintain team records; every mutation creates an audit record.
- [ ] Date-range filters operate on normalized overlapping ranges and safely omit unparseable dates.
- [ ] Templates retain existing session/CSRF handling and work at narrow viewport widths.
- [ ] README, migrations, tests and deployment all describe the same supported workflow.
