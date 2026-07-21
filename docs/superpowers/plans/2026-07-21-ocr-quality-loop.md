# OCR 质检闭环 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 自动保存人工审核前后的截图 OCR 快照，并提供一个可定位问题、可跳转审核的 OCR 质检中心。

**Architecture:** 为每次截图来源的人工审核增加不可变 `OcrReviewSample` 版本记录；审核事务先写样本、再更新 `RunItem`。新增 `app/quality.py` 将最新样本聚合为视图模型，`/quality` 路由和模板仅渲染该视图模型，不在模板执行统计逻辑。

**Tech Stack:** Python 3.12、FastAPI、SQLAlchemy 2、Alembic、Jinja2、pytest、Ruff。

---

## 文件职责

- `app/models.py`：定义 `OcrReviewSample` 及其与运行、条目、产品和用户的关系。
- `migrations/versions/0004_add_ocr_review_samples.py`：为现有 PostgreSQL/SQLite 数据库创建质检样本表和索引。
- `app/jobs/review.py`：在不提交事务的前提下创建人工审核前后的样本快照。
- `app/main.py`：在人工审核保存事务中调用快照函数，并提供受登录保护的 `/quality` 页面。
- `app/quality.py`：查询每个条目的最新样本，计算字段、产品、产品匹配、最近问题和运营指标。
- `app/templates/quality.html`：按现有侧边栏风格渲染质检中心。
- `app/templates/base.html`、`app/static/app.css`：增加导航入口和紧凑质量页面样式。
- `tests/integration/test_jobs.py`：覆盖样本快照的原子性与版本语义。
- `tests/e2e/test_lan_flow.py`：覆盖页面指标、来源排除、跳转链接和删除级联。

### Task 1: 建立质检样本模型与迁移

**Files:**
- Modify: `app/models.py:70-110`
- Create: `migrations/versions/0004_add_ocr_review_samples.py`
- Test: `tests/e2e/test_lan_flow.py`

- [ ] **Step 1: 写会失败的 ORM 关系测试**

在 `tests/e2e/test_lan_flow.py` 加入 `OcrReviewSample` 导入和以下测试；基线中模型不存在，应在导入阶段失败。

```python
def test_run_deletion_cascades_ocr_review_samples(tmp_path: Path) -> None:
    app, factory = _test_app(tmp_path)
    with TestClient(app):
        run_id, item_id, _, _ = _create_run_with_artifacts(factory, tmp_path)
        session = factory()
        try:
            sample = OcrReviewSample(
                run_id=run_id,
                run_item_id=item_id,
                excel_product_name="产品A",
                review_version=1,
                ocr_match_source="image",
                ocr_metric_values={"weekly": "0.01"},
                ocr_metric_status={"weekly": "extracted"},
                confirmed_metric_values={"weekly": "0.01"},
                confirmed_metric_status={"weekly": "manual"},
            )
            session.add(sample)
            session.commit()
            session.delete(session.get(UpdateRun, run_id))
            session.commit()
            assert session.query(OcrReviewSample).count() == 0
        finally:
            session.close()
```

- [ ] **Step 2: 运行测试，确认它因缺少模型失败**

Run: `/Users/kale/Documents/熊总/.venv/bin/pytest tests/e2e/test_lan_flow.py::test_run_deletion_cascades_ocr_review_samples -q`

Expected: FAIL，提示无法导入 `OcrReviewSample`。

- [ ] **Step 3: 定义模型、关系与迁移**

在 `app/models.py` 定义以下模型，并为 `UpdateRun` 与 `RunItem` 各增加 `quality_samples` 关系，使用 `cascade="all, delete-orphan"`。`OcrReviewSample.product` 只关联人工确认后的 `product_id`，并显式指定 `foreign_keys=[product_id]`；`ocr_product_id` 只保留审核前匹配结果的 ID，不建立第二个产品关系，避免 SQLAlchemy 因两条产品外键产生歧义：

```python
class OcrReviewSample(Base):
    __tablename__ = "ocr_review_samples"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("update_runs.id"), index=True)
    run_item_id: Mapped[int] = mapped_column(ForeignKey("run_items.id"), index=True)
    actor_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    product_id: Mapped[int | None] = mapped_column(ForeignKey("products.id"), index=True)
    excel_product_name: Mapped[str] = mapped_column(String(255))
    review_version: Mapped[int] = mapped_column()
    ocr_match_source: Mapped[str] = mapped_column(String(30))
    ocr_product_id: Mapped[int | None] = mapped_column(ForeignKey("products.id"), index=True)
    ocr_metric_values: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    ocr_metric_status: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    confirmed_metric_values: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    confirmed_metric_status: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    review_note: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
```

创建 Alembic `0004`，其 `down_revision = "0003_allow_deleted_user_history"`，创建表、外键和 `ix_ocr_review_samples_run_item_id` / `ix_ocr_review_samples_created_at` 索引。`downgrade()` 只删除该表。

- [ ] **Step 4: 运行 ORM 测试，确认关系可级联删除**

Run: `/Users/kale/Documents/熊总/.venv/bin/pytest tests/e2e/test_lan_flow.py::test_run_deletion_cascades_ocr_review_samples -q`

Expected: PASS。

- [ ] **Step 5: 提交模型和迁移**

```bash
git add app/models.py migrations/versions/0004_add_ocr_review_samples.py tests/e2e/test_lan_flow.py
git commit -m "feat: store OCR review samples"
```

### Task 2: 在人工审核事务中自动保存快照

**Files:**
- Modify: `app/jobs/review.py:30-85`
- Modify: `app/main.py:720-835`
- Test: `tests/integration/test_jobs.py`

- [ ] **Step 1: 写会失败的快照与版本测试**

在 `tests/integration/test_jobs.py` 添加测试，以 `match_source="image"`、`metric_values={"weekly": "0.01"}` 和 `metric_status={"weekly": "extracted"}` 创建 `RunItem`。两次调用新函数后应有两份样本，第二份版本为 `2`，且第一份原 OCR 快照不被改变：

```python
samples = session.query(OcrReviewSample).order_by(OcrReviewSample.review_version).all()
assert [sample.review_version for sample in samples] == [1, 2]
assert samples[0].ocr_metric_values == {"weekly": "0.01"}
assert samples[1].confirmed_metric_values == {"weekly": "0.012"}
```

再加一条 `match_source="public_provider"` 的条目，断言新函数返回 `None` 且没有创建样本。

- [ ] **Step 2: 运行测试，确认缺少快照函数失败**

Run: `/Users/kale/Documents/熊总/.venv/bin/pytest tests/integration/test_jobs.py -k 'ocr_review_sample' -q`

Expected: FAIL，提示 `capture_ocr_review_sample` 未定义。

- [ ] **Step 3: 实现快照函数并复用已解析的人工值**

在 `app/jobs/review.py` 新增：

```python
OCR_QUALITY_SOURCES = {"image", "none"}

def capture_ocr_review_sample(
    session: Session,
    *,
    run_id: int,
    item: RunItem,
    actor_id: int,
    product: Product,
    values: Mapping[str, Decimal],
    note: str,
) -> OcrReviewSample | None:
    previous = session.scalar(
        select(OcrReviewSample)
        .where(OcrReviewSample.run_item_id == item.id)
        .order_by(OcrReviewSample.review_version.desc())
    )
    if item.match_source in OCR_QUALITY_SOURCES:
        source = item.match_source
        ocr_product_id = item.product_id
        ocr_values = dict(item.metric_values)
        ocr_statuses = dict(item.metric_status)
    elif previous is not None:
        source = previous.ocr_match_source
        ocr_product_id = previous.ocr_product_id
        ocr_values = dict(previous.ocr_metric_values)
        ocr_statuses = dict(previous.ocr_metric_status)
    else:
        return None
    version = (previous.review_version if previous is not None else 0) + 1
    sample = OcrReviewSample(
        run_id=run_id,
        run_item_id=item.id,
        actor_id=actor_id,
        product_id=product.id,
        excel_product_name=str(item.original_values.get("product_name", "")),
        review_version=version,
        ocr_match_source=source,
        ocr_product_id=ocr_product_id,
        ocr_metric_values=ocr_values,
        ocr_metric_status=ocr_statuses,
        confirmed_metric_values={name: str(value) for name, value in values.items()},
        confirmed_metric_status={
            field.name: "manual" if field.name in values else "unconfirmed"
            for field in METRIC_FIELDS
        },
        review_note=note.strip(),
    )
    session.add(sample)
    return sample
```

将 `save_manual_review` 改为接收可选的已解析 `values`，避免路由再次解析表单。路由在确认产品后依次执行：`values = parse_manual_metrics(inputs)`、`capture_ocr_review_sample(...)`、`save_manual_review(..., values=values)`、既有 `AuditLog`、最后仅一次 `session.commit()`。不要在快照函数中提交事务。重复审核时新样本复用上一版本的原 OCR 快照，而不把当前 `manual` 值误作 OCR 识别结果。

- [ ] **Step 4: 运行快照测试及已有人工审核测试**

Run: `/Users/kale/Documents/熊总/.venv/bin/pytest tests/integration/test_jobs.py -k 'ocr_review_sample or manual_review' -q`

Expected: PASS。

- [ ] **Step 5: 写会失败的路由原子性测试**

在 `tests/e2e/test_lan_flow.py` 对已登录用户创建截图来源待审核条目。通过 `event.listen(factory.class_, "before_commit", reject_sample)` 监听测试应用每个请求使用的 Session；当 `OcrReviewSample` 位于 `session.new` 时抛出 `SQLAlchemyError("sample write failed")`。在 `finally` 中用 `event.remove(factory.class_, "before_commit", reject_sample)` 清除监听器。提交审核请求后，使用新 Session 断言 HTTP 500、`RunItem.match_source != "manual"` 且样本数为 `0`。

- [ ] **Step 6: 运行路由原子性测试，确认当前事务边界失败**

Run: `/Users/kale/Documents/熊总/.venv/bin/pytest tests/e2e/test_lan_flow.py -k 'review_sample_is_atomic' -q`

Expected: FAIL，直到路由把快照和审核放在同一个提交中并在提交失败后回滚。

- [ ] **Step 7: 在保存失败时回滚并返回审核页面**

在 `save_review` 的提交周围仅捕获 `SQLAlchemyError`，调用 `session.rollback()`，并通过现有 `review_response(..., error="保存审核失败，请重试", status_code=500, draft_item_id=item_id, draft=draft)` 返回。这样样本、审核值和审计记录不会部分提交。

- [ ] **Step 8: 运行原子性测试确认通过并提交**

Run: `/Users/kale/Documents/熊总/.venv/bin/pytest tests/e2e/test_lan_flow.py -k 'review_sample_is_atomic' -q`

Expected: PASS。

```bash
git add app/jobs/review.py app/main.py tests/integration/test_jobs.py tests/e2e/test_lan_flow.py
git commit -m "feat: capture OCR review feedback"
```

### Task 3: 构建可测试的质量聚合服务

**Files:**
- Create: `app/quality.py`
- Test: `tests/unit/test_quality.py`

- [ ] **Step 1: 写会失败的字段统计测试**

创建 `tests/unit/test_quality.py`，给 `weekly` 建三条最新样本：一条 OCR 与人工均为 `"0.01"`、一条 OCR 缺失且人工为 `"0.02"`、一条 OCR 为 `"0.03"` 而人工为 `"0.04"`。测试 `build_quality_dashboard(session, now=datetime(2026, 7, 21))`：

```python
weekly = dashboard.fields[0]
assert weekly.confirmed_count == 3
assert weekly.matched_count == 1
assert weekly.missing_count == 1
assert weekly.incorrect_count == 1
assert weekly.accuracy == Decimal("0.3333")
```

并为同一 `run_item_id` 加入更旧的样本，断言旧版本不改变统计结果；为 31 天前样本加入不同值，断言默认 30 天窗口不统计它。

- [ ] **Step 2: 运行测试，确认聚合服务不存在**

Run: `/Users/kale/Documents/熊总/.venv/bin/pytest tests/unit/test_quality.py -q`

Expected: FAIL，提示模块 `app.quality` 不存在。

- [ ] **Step 3: 定义视图模型和聚合函数**

在 `app/quality.py` 定义不可变 dataclass：

```python
@dataclass(frozen=True)
class QualityBreakdown:
    key: str
    label: str
    confirmed_count: int
    matched_count: int
    missing_count: int
    incorrect_count: int
    accuracy: Decimal | None

@dataclass(frozen=True)
class QualityIssue:
    run_id: int
    run_item_id: int
    product_name: str
    metric_label: str
    outcome: str
    reviewed_at: datetime

@dataclass(frozen=True)
class QualityDashboard:
    field_accuracy: Decimal | None
    pending_review_count: int
    source_blank_count: int
    missing_count: int
    product_matched_count: int
    product_unmatched_count: int
    product_corrected_count: int
    fields: tuple[QualityBreakdown, ...]
    products: tuple[QualityBreakdown, ...]
    recent_issues: tuple[QualityIssue, ...]
```

实现 `build_quality_dashboard(session, *, now: datetime | None = None, days: int = 30)`：先用 `run_item_id + max(review_version)` 子查询选择最新样本，再按 `created_at >= now - timedelta(days=days)` 过滤。只比较 `confirmed_metric_status[metric] == "manual"` 的字段：OCR 值不存在即 `missing_count += 1`；存在且字符串相同即匹配；否则值不一致。用 `Decimal(matched_count) / Decimal(confirmed_count)` 后量化到 `Decimal("0.0001")`。

使用 `UpdateRun.created_at` 窗口查询所有当前 `RunItem` 的 `metric_status`，计数 `source_blank`；使用当前 `row_status in {"needs_review", "stale", "failed"}` 计数待审核。对每份最新样本，`ocr_product_id == product_id` 计入产品匹配，`ocr_product_id is None` 计入未匹配，其余计入人工改正。按字段、确认产品名聚合；最近问题仅包含漏识别和值不一致的最新样本，按审核时间倒序保留 10 条。

- [ ] **Step 4: 运行质量服务测试**

Run: `/Users/kale/Documents/熊总/.venv/bin/pytest tests/unit/test_quality.py -q`

Expected: PASS。

- [ ] **Step 5: 提交质量服务**

```bash
git add app/quality.py tests/unit/test_quality.py
git commit -m "feat: summarize OCR quality feedback"
```

### Task 4: 添加质检中心路由、页面与导航

**Files:**
- Modify: `app/main.py:15-70, 200-280`
- Modify: `app/templates/base.html:14-23`
- Create: `app/templates/quality.html`
- Modify: `app/static/app.css`
- Test: `tests/e2e/test_lan_flow.py`

- [ ] **Step 1: 写会失败的页面测试**

在 `tests/e2e/test_lan_flow.py` 创建一条截图来源的样本、一条 `source_blank` 的当前条目和一条待审核条目。登录后访问 `/quality`，断言页面包含：

```python
assert "质检中心" in response.text
assert "字段一致率" in response.text
assert "漏识别" in response.text
assert "source_blank" not in response.text
assert f'/updates/{run_id}/review#review-item-{item_id}' in response.text
```

同时访问 `/quality` 前不登录，断言重定向到 `/login`。

- [ ] **Step 2: 运行页面测试，确认路由不存在**

Run: `/Users/kale/Documents/熊总/.venv/bin/pytest tests/e2e/test_lan_flow.py -k 'quality_center' -q`

Expected: FAIL，`/quality` 尚未定义。

- [ ] **Step 3: 实现只读页面**

在 `app/main.py` 导入 `build_quality_dashboard`，增加：

```python
@app.get("/quality", response_class=HTMLResponse)
def quality_page(
    request: Request,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    return templates.TemplateResponse(
        request=request,
        name="quality.html",
        context={
            "user": user,
            "quality": build_quality_dashboard(session),
            "csrf_token": csrf_token(request),
        },
    )
```

在 `base.html` 的“新建更新”之后增加 `/quality` 导航，使用 `request.url.path == '/quality'` 控制激活状态。

`quality.html` 用五个紧凑统计块显示一致率、待审核、漏识别、“已确认空值”和“产品已改正”，再用两个表格显示字段和产品分组，最后显示最近问题。空数据用“暂未积累人工确认样本”替代百分比。链接只能使用 `QualityIssue.run_id` 和 `run_item_id` 构造审核锚点。

在 `app.css` 添加 `.quality-kpis`、`.quality-kpi`、`.quality-section` 和移动端单列规则；复用现有 `.table-wrap`、`.status` 和按钮样式，不改动其他页面布局。

- [ ] **Step 4: 运行页面测试确认通过**

Run: `/Users/kale/Documents/熊总/.venv/bin/pytest tests/e2e/test_lan_flow.py -k 'quality_center' -q`

Expected: PASS。

- [ ] **Step 5: 提交页面实现**

```bash
git add app/main.py app/templates/base.html app/templates/quality.html app/static/app.css tests/e2e/test_lan_flow.py
git commit -m "feat: add OCR quality center"
```

### Task 5: 全量验证、审阅和部署

**Files:**
- Modify: `docs/superpowers/plans/2026-07-21-ocr-quality-loop.md`
- Test: `tests/`

- [ ] **Step 1: 运行完整验证**

Run:

```bash
/Users/kale/Documents/熊总/.venv/bin/pytest -q
/Users/kale/Documents/熊总/.venv/bin/ruff check app tests migrations
git diff main...HEAD --check
```

Expected: pytest 全部通过、Ruff 无问题、diff 无空白错误。

- [ ] **Step 2: 审阅改动范围**

Run: `git diff --stat main...HEAD`

Expected: 改动仅包含质检样本、迁移、审核流程、质检服务、模板、样式和测试。

- [ ] **Step 3: 提交计划文档**

```bash
git add docs/superpowers/plans/2026-07-21-ocr-quality-loop.md
git commit -m "docs: add OCR quality implementation plan"
```

- [ ] **Step 4: 合并并部署**

在主工作区快进合并功能分支，重新运行完整验证，推送 `main`。同步到 `/mnt/user/appdata/nav-updater/` 时保留 `.env`，再以现有 `nav-updater-app:latest` 为基础覆盖 `app/`、`migrations/`、`alembic.ini` 和 `entrypoint.sh` 快速构建新镜像。将镜像同时标记为 `nav-updater-app:latest` 和 `nav-updater-worker:latest`，以 `docker compose up -d --no-build --force-recreate` 启动。

Run:

```bash
ssh root@192.168.5.28 'cd /mnt/user/appdata/nav-updater && docker compose ps && curl -fsS http://127.0.0.1:8080/healthz'
```

Expected: `app`、`worker`、`db` 为运行状态，健康端点返回 `{"status":"ok"}`。
