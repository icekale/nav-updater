# 人工审核目录补齐 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让目录外的私募产品能在人工审核中安全建档，并优先展示 OCR 不完整的条目。

**Architecture:** `app.catalog` 新增名称匹配和私募产品创建服务。审核路由只在用户提交 `create_private` 时根据 Excel 原始名称调用服务；模板接收产品选择状态、缺失指标和 `show_all` 过滤状态。现有 `RunItem`、人工审核和 Excel 重生成流程不改。

**Tech Stack:** Python 3.12, FastAPI/Jinja2, SQLAlchemy 2, pytest, Ruff, Docker Compose.

---

## File Map

- Modify: `app/catalog.py` — 按规范化名称匹配激活产品，并生成稳定的私募内部代码。
- Modify: `app/main.py` — 生成审核行视图模型，解析 `product_choice`，写入自动建档审计记录。
- Modify: `app/templates/review.html` — 渲染待审核过滤、自动建档选项和待补指标。
- Modify: `app/static/app.css` — 添加审核摘要和待补字段样式。
- Modify: `tests/integration/test_jobs.py` — 覆盖目录身份服务。
- Modify: `tests/e2e/test_lan_flow.py` — 覆盖自动建档和审核页过滤。
- Modify: `README.md` — 记录自动私募建档的使用规则。

## Task 1: Implement Private Product Identity

**Files:** `app/catalog.py`, `tests/integration/test_jobs.py`

- [ ] **Step 1: Write failing catalog tests.**

Append these tests with the existing in-memory SQLAlchemy setup:

```python
from app.catalog import (
    PrivateProductError,
    get_or_create_private_product,
    matching_active_products,
    private_product_code,
)


def make_session() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def test_get_or_create_private_product_uses_a_stable_code() -> None:
    session = make_session()
    product, created = get_or_create_private_product(session, "仁桥 金选泽源5B")
    assert created is True
    assert product.product_name == "仁桥 金选泽源5B"
    assert product.product_type == "private"
    assert product.product_code.startswith("private-")
    assert len(product.product_code) == len("private-") + 12

    reused, reused_created = get_or_create_private_product(session, "仁桥金选泽源5B")
    assert (reused.id, reused_created) == (product.id, False)


def test_get_or_create_private_product_rejects_ambiguous_name() -> None:
    session = make_session()
    session.add_all(
        [
            Product(product_name="产品A", product_code="P001", product_type="private"),
            Product(product_name="产品B", product_code="P002", product_type="private", historical_names=["产品A"]),
        ]
    )
    session.commit()

    assert len(matching_active_products(session, "产品A")) == 2
    with pytest.raises(PrivateProductError, match="多个激活产品"):
        get_or_create_private_product(session, "产品A")
```

- [ ] **Step 2: Verify the tests fail.**

Run:

```bash
.venv/bin/pytest tests/integration/test_jobs.py::test_get_or_create_private_product_uses_a_stable_code tests/integration/test_jobs.py::test_get_or_create_private_product_rejects_ambiguous_name -q
```

Expected: FAIL because these catalog APIs do not exist.

- [ ] **Step 3: Implement the catalog APIs.**

Add `hashlib` and `normalize_name` imports to `app/catalog.py`, then add this production code after `import_catalog()`:

```python
class PrivateProductError(ValueError):
    pass


def matching_active_products(session: Session, product_name: str) -> list[Product]:
    normalized = normalize_name(product_name)
    if not normalized:
        return []
    return [
        product
        for product in session.scalars(select(Product).where(Product.is_active.is_(True))).all()
        if normalize_name(product.product_name) == normalized
        or any(normalize_name(alias) == normalized for alias in product.historical_names or [])
    ]


def private_product_code(product_name: str) -> str:
    normalized = normalize_name(product_name)
    if not normalized:
        raise PrivateProductError("Excel 产品名称不能为空")
    return f"private-{hashlib.sha256(normalized.encode()).hexdigest()[:12]}"


def get_or_create_private_product(session: Session, product_name: str) -> tuple[Product, bool]:
    matches = matching_active_products(session, product_name)
    if len(matches) == 1:
        return matches[0], False
    if len(matches) > 1:
        raise PrivateProductError("多个激活产品与 Excel 产品名称匹配，请明确选择产品")
    code = private_product_code(product_name)
    if session.scalar(select(Product).where(Product.product_code == code)) is not None:
        raise PrivateProductError("内部产品编号冲突")
    product = Product(product_name=product_name.strip(), product_code=code, product_type="private")
    session.add(product)
    session.flush()
    return product, True
```

Do not update names, aliases, codes, types or activation state of any existing product.

- [ ] **Step 4: Verify and commit.**

Run:

```bash
.venv/bin/pytest tests/integration/test_jobs.py -q
.venv/bin/ruff check app/catalog.py tests/integration/test_jobs.py
```

Expected: all integration job tests pass and Ruff has no output.

```bash
git add app/catalog.py tests/integration/test_jobs.py
git commit -m "feat: create private products during review"
```

## Task 2: Prepare Review Product Choices

**Files:** `app/main.py`, `tests/e2e/test_lan_flow.py`

- [ ] **Step 1: Write a failing browser-flow test.**

Add an authenticated test with one `needs_review` item lacking a catalog product and one `ready` item:

```python
def logged_in_client(tmp_path: Path) -> tuple[TestClient, sessionmaker]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    client = TestClient(
        create_app(
            settings=Settings(
                database_url="sqlite+pysqlite:///:memory:",
                data_dir=tmp_path,
                session_secret="test-secret",
                initial_admin_username="admin",
                initial_admin_password="change-me",
            ),
            session_factory=factory,
        )
    )
    client.__enter__()
    page = client.get("/login")
    token = re.search(r'name="token" value="([^"]+)"', page.text).group(1)
    client.post("/login", data={"username": "admin", "password": "change-me", "token": token})
    return client, factory


def make_review_run(factory: sessionmaker, name: str, metric_values: dict[str, str], metric_status: dict[str, str]) -> tuple[int, int, int]:
    session = factory()
    try:
        admin = session.query(User).filter_by(username="admin").one()
        run = UpdateRun(operator_id=admin.id, cutoff_date=date(2026, 7, 17), status="completed")
        session.add(run)
        session.flush()
        review_item = RunItem(
            run_id=run.id,
            excel_row=2,
            original_values={"product_name": name},
            row_status="needs_review",
            metric_values=metric_values,
            metric_status=metric_status,
        )
        ready_item = RunItem(
            run_id=run.id,
            excel_row=3,
            original_values={"product_name": "完整产品"},
            row_status="ready",
        )
        session.add_all([review_item, ready_item])
        session.commit()
        return run.id, review_item.id, ready_item.id
    finally:
        session.close()


def test_review_creates_private_product_and_hides_ready_items(tmp_path: Path) -> None:
    client, factory = logged_in_client(tmp_path)
    try:
        run_id, item_id, ready_item_id = make_review_run(
            factory,
            name="测试私募1号",
            metric_values={"weekly": "0.0123"},
            metric_status={"weekly": "extracted", "mtd": "stale"},
        )

        review = client.get(f"/updates/{run_id}/review")
        assert "测试私募1号" in review.text
        assert str(ready_item_id) not in review.text
        assert 'value="create_private" selected' in review.text

        token = re.search(r'name="token" value="([^"]+)"', review.text).group(1)
        saved = client.post(
            f"/updates/{run_id}/items/{item_id}/review",
            data={
                "token": token,
                "product_choice": "create_private",
                "weekly": "1.23",
                "mtd": "2.34",
                "review_note": "核对管理人净值表",
            },
            follow_redirects=False,
        )
        assert saved.status_code == 303

        session = factory()
        try:
            product = session.query(Product).filter_by(product_name="测试私募1号").one()
            assert product.product_type == "private"
            assert session.get(RunItem, item_id).product_id == product.id
            assert session.query(AuditLog).filter_by(action="create_private_product").count() == 1
        finally:
            session.close()
    finally:
        client.__exit__(None, None, None)


def test_review_keeps_submitted_values_after_private_code_conflict(tmp_path: Path) -> None:
    client, factory = logged_in_client(tmp_path)
    run_id, item_id, _ = make_review_run(factory, name="测试私募冲突", metric_values={}, metric_status={})
    session = factory()
    try:
        session.add(Product(product_name="其他产品", product_code=private_product_code("测试私募冲突"), product_type="private"))
        session.commit()
    finally:
        session.close()

    review = client.get(f"/updates/{run_id}/review")
    token = re.search(r'name="token" value="([^"]+)"', review.text).group(1)
    failed = client.post(
        f"/updates/{run_id}/items/{item_id}/review",
        data={"token": token, "product_choice": "create_private", "weekly": "1.23", "review_note": "保留这段说明"},
    )
    assert failed.status_code == 422
    assert 'value="1.23"' in failed.text
    assert "保留这段说明" in failed.text
```

Add the helper imports (`date`, `re`, `Path`, `StaticPool`, `TestClient`, `create_engine`, `Session`, `sessionmaker`, `Settings`, `create_app`, `AuditLog`, `Product`, `RunItem`, `UpdateRun`, `User`) at the top of the test module. The test closes the TestClient after assertions so FastAPI startup/shutdown events run normally.

- [ ] **Step 2: Verify the test fails.**

Run:

```bash
.venv/bin/pytest tests/e2e/test_lan_flow.py::test_review_creates_private_product_and_hides_ready_items -q
```

Expected: FAIL because the page currently shows all items and accepts only `product_id`.

- [ ] **Step 3: Add review-view preparation and choice parsing.**

In `app/main.py`:

1. Import `PrivateProductError`, `get_or_create_private_product`, and `matching_active_products` from `app.catalog`.
2. Define `REVIEWABLE_STATUSES = {"needs_review", "stale", "failed"}` next to `ATTENDANCE_OPTIONS`.
3. Add a helper returning the following review row mapping. It uses `item.product_id` first; otherwise it uses the unique result from `matching_active_products(session, Excel 原名)`, selects `create_private` for no result, and leaves selection blank for multiple results. Its optional `draft` mapping supplies submitted metric values, product choice and review note for one failed form submission.

```python
{
    "item": item,
    "metric_values": formatted_metric_values(item),
    "selected_choice": "product:<id>" | "create_private" | "",
    "can_create_private": bool,
    "review_note": str,
    "missing_metrics": {
        field.name
        for field in METRIC_FIELDS
        if item.metric_status.get(field.name) in {"stale", "insufficient_data", "failed"}
    },
}
```

4. Add `show_all: bool = False`, `draft_item_id: int | None = None`, and `draft: Mapping[str, str] | None = None` to `review_response()`. Pass `draft` only to the matching item row. Add `show_all: bool = False` to `GET /updates/{run_id}/review`. When false, pass only items whose `row_status` is in `REVIEWABLE_STATUSES`; always pass `pending_count` and `show_all` to the template.
5. Replace `product_id: int = Form(...)` in the review-save endpoint with `product_choice: str = Form(...)`. For `product:<id>`, load the active product. For `create_private`, call `get_or_create_private_product(session, Excel 原名)`. Reject any other value with the existing 422 template response.
6. When a product was created, add this audit event before the current manual-review audit event:

```python
AuditLog(
    actor_id=user.id,
    action="create_private_product",
    object_type="product",
    object_id=str(product.id),
    context={"product_name": product.product_name, "product_code": product.product_code, "run_id": run.id},
)
```

7. Read `form = await request.form()` before resolving the product. Construct `draft = {field.name: str(form.get(field.name, "")) for field in METRIC_FIELDS} | {"product_choice": product_choice, "review_note": review_note}`. Catch `PrivateProductError` alongside `ManualReviewError` and return `review_response(..., status_code=422, draft_item_id=item_id, draft=draft)`. Leave `save_manual_review()` unchanged because it still receives a real `Product`.

- [ ] **Step 4: Verify the route behavior and commit.**

Run:

```bash
.venv/bin/pytest tests/e2e/test_lan_flow.py::test_review_creates_private_product_and_hides_ready_items tests/e2e/test_lan_flow.py::test_user_can_manually_review_and_regenerate_a_run tests/e2e/test_lan_flow.py::test_manual_review_is_rejected_while_run_is_processing -q
```

Expected: all tests pass. Update the existing manual-review test to send `product_choice=f"product:{product_id}"`.

```bash
git add app/main.py tests/e2e/test_lan_flow.py
git commit -m "feat: bootstrap products from review"
```

## Task 3: Render Missing Metrics And Review Filter

**Files:** `app/templates/review.html`, `app/static/app.css`, `tests/e2e/test_lan_flow.py`

- [ ] **Step 1: Extend the browser test with view assertions.**

Add these assertions to the task-2 test:

```python
assert f'/updates/{run_id}/review?show_all=1' in review.text
assert 'class="metric-field missing"' in review.text
all_items = client.get(f"/updates/{run_id}/review?show_all=1")
assert "已识别完整" in all_items.text
assert "完整产品" in all_items.text
```

- [ ] **Step 2: Verify the test fails.**

Run:

```bash
.venv/bin/pytest tests/e2e/test_lan_flow.py::test_review_creates_private_product_and_hides_ready_items -q
```

Expected: FAIL because the existing template has no filter summary, toggle link, automatic option, or missing-field class.

- [ ] **Step 3: Implement the template and styles.**

In `app/templates/review.html`:

```html
<p class="review-summary">待处理 {{ pending_count }} 条 · {% if show_all %}<a href="/updates/{{ run.id }}/review">仅看待审核</a>{% else %}<a href="/updates/{{ run.id }}/review?show_all=1">显示已识别完整条目</a>{% endif %}</p>
```

Replace the current product control with `name="product_choice"`; render `create_private` only for `row.can_create_private`, and render active products as `value="product:{{ product.id }}"`. Give each metric label the class below and show `待补` before its input when the field is in `row.missing_metrics`:

```html
<label class="metric-field{% if field.name in row.missing_metrics %} missing{% endif %}">
  {{ field.label }}{% if field.name in row.missing_metrics %}<span class="metric-flag">待补</span>{% endif %}
  <input type="text" name="{{ field.name }}" inputmode="decimal" value="{{ row.metric_values[field.name] }}">
</label>
```

Render the draft-aware note with `{{ row.review_note }}` and use `row.selected_choice` for every product option, including `create_private`.

Add these scoped styles to `app/static/app.css`:

```css
.review-summary { display: flex; align-items: center; gap: 12px; color: #71808c; font-size: 13px; margin: -12px 0 18px; }
.review-summary a { color: #1f5d89; }
.metric-field { position: relative; }
.metric-field.missing input { border-color: #c97878; background: #fff7f7; }
.metric-flag { color: #a23c3c; font-size: 11px; font-weight: 700; }
```

Keep recognized metric values editable. Use “当前没有待审核条目” for the default empty list and “该批次没有产品行” for the full-list empty state.

- [ ] **Step 4: Verify and commit UI work.**

Run:

```bash
.venv/bin/pytest tests/e2e/test_lan_flow.py -q
.venv/bin/ruff check app/main.py tests/e2e/test_lan_flow.py
```

Expected: all LAN-flow tests pass and Ruff has no output.

```bash
git add app/templates/review.html app/static/app.css tests/e2e/test_lan_flow.py
git commit -m "feat: focus manual review on missing metrics"
```

## Task 4: Document, Verify And Deploy

**Files:** `README.md`

- [ ] **Step 1: Document the workflow.**

Add this text beneath the existing manual-review instructions:

```markdown
当截图已匹配但私募产品不在目录中，人工审核页会默认“自动创建私募产品”。保存审核时，系统以 Excel 原始产品名称创建稳定内部编号的私募目录项；不会依据 OCR 识别出的名称自动建档。后续同名批次会复用该产品。
```

- [ ] **Step 2: Run final local verification.**

Run:

```bash
.venv/bin/pytest -q
.venv/bin/ruff check .
git diff --check
docker build --platform linux/amd64 -t nav-updater:review-catalog-bootstrap .
```

Expected: all tests pass, Ruff and `git diff --check` have no output, and the Docker build exits with status 0.

- [ ] **Step 3: Commit, push, deploy and verify.**

```bash
git add README.md
git commit -m "docs: explain review product bootstrap"
git push origin main
ssh root@192.168.5.28 'cd /mnt/user/appdata/nav-updater && git pull --ff-only && docker compose up -d --build'
ssh root@192.168.5.28 'curl -fsS http://127.0.0.1:8080/healthz'
```

Expected: GitHub receives the commit, Unraid fast-forwards and recreates `app`/`worker`, and health returns `{"status":"ok"}`.

## Self-Review Checklist

- [ ] The automatic path uses only the Excel source name, never the OCR name.
- [ ] A directory item is created only when the reviewer saves `create_private`.
- [ ] Existing unique products are reused and ambiguous products require an explicit choice.
- [ ] Invalid product selection and private-code conflicts retain the submitted metrics and review note.
- [ ] Default review view includes only `needs_review`, `stale` and `failed`; all rows remain accessible.
- [ ] Extracted values stay editable and missing values receive a visible marker.
- [ ] Both automatic creation and manual review save audit records.
- [ ] No migration, SPA, OCR-coordinate persistence, or public-fund behavior change is introduced.
