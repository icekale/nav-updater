# 工作台操作与侧边栏布局 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为投研净值更新工具增加安全的批次与账号删除、准确的 OCR 确认统计与分流，以及响应式侧边栏工作台界面。

**Architecture:** 业务规则保留在 FastAPI 服务端：删除路由在同一事务中处理关联记录和审计，文件删除由受限路径助手完成；预览把 OCR 的数值和已确认空值预先转换为展示数据。数据库用一条 Alembic 迁移将操作者外键改为可空，以保留删除账号后的批次历史。模板仍使用 Jinja 服务端渲染，CSS 仅替换公共应用壳与页面操作样式。

**Tech Stack:** Python 3.12、FastAPI、SQLAlchemy、Alembic、Jinja2、pytest、Ruff。

---

## 文件职责

- `app/models.py`：声明删除账号后的 `SET NULL` 外键语义。
- `migrations/versions/0003_allow_deleted_user_history.py`：为已有 SQLite 与 PostgreSQL 数据库应用安全的外键迁移。
- `app/jobs/service.py`：提供单一、可测试的批次删除服务与限定的数据目录文件清理。
- `app/jobs/processor.py`：以确认字段覆盖率优先决定 OCR 行状态，并保留低置信度提示。
- `app/main.py`：增加删除路由、预览展示模型、删除权限检查和一次性提示传递。
- `app/templates/base.html`、`updates.html`、`admin_users.html`、`preview.html`：渲染侧边栏、删除按钮和 OCR 确认统计。
- `app/static/app.css`：实现桌面侧边栏与小屏顶部紧凑导航，并为危险操作提供低强调样式。
- `tests/integration/test_jobs.py`：覆盖高覆盖、低置信度 OCR 的非阻塞分流。
- `tests/e2e/test_lan_flow.py`：覆盖批次删除、账号删除限制、预览文案和侧边栏页面。

本计划的端到端测试在 `tests/e2e/test_lan_flow.py` 现有 imports 后追加以下可复用测试助手，避免每个场景重复初始化内存数据库：

```python
def page_token(response) -> str:
    return re.search(r'name="token" value="([^"]+)"', response.text).group(1)


def signed_in_client(tmp_path: Path) -> tuple[TestClient, sessionmaker]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    settings = Settings(
        database_url="sqlite+pysqlite:///:memory:",
        data_dir=tmp_path,
        session_secret="test-secret",
        initial_admin_username="admin",
        initial_admin_password="change-me",
    )
    session = factory()
    try:
        session.add(User(username="admin", password_hash=hash_password("change-me"), role="admin"))
        session.commit()
    finally:
        session.close()
    client = TestClient(create_app(settings=settings, session_factory=factory))
    login = client.post(
        "/login",
        data={"username": "admin", "password": "change-me", "token": page_token(client.get("/login"))},
        follow_redirects=False,
    )
    assert login.status_code == 303
    return client, factory


def completed_run_with_artifacts(
    factory: sessionmaker, data_dir: Path, status: str = "completed"
) -> tuple[int, Path, Path]:
    session = factory()
    try:
        admin = session.scalar(select(User).where(User.username == "admin"))
        run_dir = data_dir / "runs" / "test-run"
        run_dir.mkdir(parents=True, exist_ok=True)
        workbook = run_dir / "input.xlsx"
        image = run_dir / "source.png"
        result = run_dir / "result.xlsx"
        workbook.write_bytes(b"input")
        image.write_bytes(b"image")
        result.write_bytes(b"result")
        run = UpdateRun(operator_id=admin.id, cutoff_date=date(2026, 7, 17), status=status, output_path=str(result))
        session.add(run)
        session.flush()
        session.add_all((
            RunFile(run_id=run.id, file_type="workbook", original_name="input.xlsx", storage_path=str(workbook), sha256="0" * 64),
            RunFile(run_id=run.id, file_type="image", original_name="source.png", storage_path=str(image), sha256="1" * 64),
            RunItem(run_id=run.id, excel_row=2, original_values={"product_name": "产品A"}),
            AuditLog(actor_id=admin.id, action="create", object_type="update_run", object_id=str(run.id)),
            AuditLog(actor_id=admin.id, action="manual_review", object_type="run_item", object_id=str(run.id)),
        ))
        session.commit()
        return run.id, run_dir, result
    finally:
        session.close()
```

### Task 1: 数据库删除语义

**Files:**
- Modify: `app/models.py:49-99`
- Create: `migrations/versions/0003_allow_deleted_user_history.py`
- Test: `tests/e2e/test_lan_flow.py`

- [ ] **Step 1: 写入外键删除语义的失败单元测试**

```python
def test_user_history_foreign_keys_use_set_null() -> None:
    assert next(
        foreign_key.ondelete
        for foreign_key in UpdateRun.__table__.foreign_keys
        if foreign_key.parent.name == "operator_id"
    ) == "SET NULL"
    assert next(
        foreign_key.ondelete
        for foreign_key in AuditLog.__table__.foreign_keys
        if foreign_key.parent.name == "actor_id"
    ) == "SET NULL"
    assert UpdateRun.__table__.c.operator_id.nullable is True
```

- [ ] **Step 2: 运行该测试确认失败**

Run: `/Users/kale/Documents/熊总/.venv/bin/pytest tests/e2e/test_lan_flow.py::test_user_history_foreign_keys_use_set_null -q`

Expected: FAIL，因为模型目前未声明 `SET NULL` 且 `operator_id` 不是可空列。

- [ ] **Step 3: 定义可空、SET NULL 外键及迁移**

```python
# app/models.py
operator_id: Mapped[int | None] = mapped_column(
    ForeignKey("users.id", ondelete="SET NULL"), index=True
)
actor_id: Mapped[int | None] = mapped_column(
    ForeignKey("users.id", ondelete="SET NULL"), index=True
)
```

迁移必须在 SQLite 中用批处理重建 `update_runs` 与 `audit_logs` 外键，保留所有列和索引；在其他数据库中删除旧外键、把 `operator_id` 改为 nullable、再创建 `ON DELETE SET NULL` 外键。

- [ ] **Step 4: 运行模型测试确认通过**

Run: `/Users/kale/Documents/熊总/.venv/bin/pytest tests/e2e/test_lan_flow.py::test_user_history_foreign_keys_use_set_null -q`

Expected: PASS；迁移会在 Task 5 对真实新数据库执行验证。

- [ ] **Step 5: 提交数据库语义**

```bash
git add app/models.py migrations/versions/0003_allow_deleted_user_history.py tests/e2e/test_lan_flow.py
git commit -m "feat: retain history after user deletion"
```

### Task 2: 批次和账号的安全删除路由

**Files:**
- Modify: `app/jobs/service.py:1-180`
- Modify: `app/main.py:1-180, 167-186, 829-877`
- Test: `tests/e2e/test_lan_flow.py`

- [ ] **Step 1: 写入失败的批次删除、处理中拒绝和账号保护测试**

```python
def test_admin_deletes_other_user_and_retains_run_history(tmp_path: Path) -> None:
    client, factory = signed_in_client(tmp_path)
    session = factory()
    try:
        operator = User(username="operator", password_hash="hash", role="user")
        session.add(operator)
        session.flush()
        run = UpdateRun(operator_id=operator.id, cutoff_date=date(2026, 7, 17))
        session.add(run)
        session.add(AuditLog(actor_id=operator.id, action="create", object_type="update_run", object_id="1"))
        session.commit()
        operator_id, run_id = operator.id, run.id
    finally:
        session.close()
    token = page_token(client.get("/admin/users"))
    deleted = client.post(f"/admin/users/{operator_id}/delete", data={"token": token}, follow_redirects=False)
    assert deleted.status_code == 303
    session = factory()
    try:
        assert session.get(User, operator_id) is None
        assert session.get(UpdateRun, run_id).operator_id is None
        assert session.scalar(select(AuditLog).where(AuditLog.object_id == "1")).actor_id is None
    finally:
        session.close()
    assert "已删除账号" in client.get("/updates").text

def test_deleting_completed_run_removes_its_files_items_and_old_audit_logs(tmp_path: Path) -> None:
    client, factory = signed_in_client(tmp_path)
    run_id, run_dir, result_path = completed_run_with_artifacts(factory, tmp_path)
    deleted = client.post(f"/updates/{run_id}/delete", data={"token": page_token(client.get("/updates"))}, follow_redirects=False)
    assert deleted.headers["location"].startswith("/updates?notice=")
    session = factory()
    try:
        assert session.get(UpdateRun, run_id) is None
        assert session.scalars(select(RunItem).where(RunItem.run_id == run_id)).all() == []
        assert session.scalars(select(RunFile).where(RunFile.run_id == run_id)).all() == []
        logs = session.scalars(select(AuditLog).where(AuditLog.object_id == str(run_id))).all()
        assert [(log.action, log.object_type) for log in logs] == [("delete", "update_run")]
    finally:
        session.close()
    assert not run_dir.exists()
    assert not result_path.exists()

def test_deleting_run_does_not_remove_path_outside_data_directory(tmp_path: Path) -> None:
    client, factory = signed_in_client(tmp_path)
    run_id, _, _ = completed_run_with_artifacts(factory, tmp_path)
    outside_path = tmp_path.parent / "must-keep.xlsx"
    outside_path.write_bytes(b"keep")
    session = factory()
    try:
        session.add(RunFile(run_id=run_id, file_type="workbook", original_name="keep.xlsx", storage_path=str(outside_path), sha256="2" * 64))
        session.commit()
    finally:
        session.close()
    response = client.post(f"/updates/{run_id}/delete", data={"token": page_token(client.get("/updates"))})
    assert response.status_code == 200
    assert outside_path.read_bytes() == b"keep"

def test_deleting_processing_run_is_rejected(tmp_path: Path) -> None:
    client, factory = signed_in_client(tmp_path)
    run_id, run_dir, _ = completed_run_with_artifacts(factory, tmp_path, status="processing")
    rejected = client.post(f"/updates/{run_id}/delete", data={"token": page_token(client.get("/updates"))})
    assert rejected.status_code == 409
    assert factory().get(UpdateRun, run_id) is not None
    assert run_dir.exists()

def test_admin_cannot_delete_self_or_last_admin(tmp_path: Path) -> None:
    client, factory = signed_in_client(tmp_path)
    session = factory()
    try:
        admin = session.scalar(select(User).where(User.username == "admin"))
        admin_id = admin.id
    finally:
        session.close()
    self_delete = client.post(f"/admin/users/{admin_id}/delete", data={"token": page_token(client.get("/admin/users"))})
    assert self_delete.status_code == 409
    assert factory().get(User, admin_id) is not None
```

- [ ] **Step 2: 运行三项测试确认失败**

Run: `/Users/kale/Documents/熊总/.venv/bin/pytest tests/e2e/test_lan_flow.py -k 'deleting_completed_run or deleting_processing_run or cannot_delete_self_or_last_admin' -q`

Expected: FAIL，因为路由与清理服务尚不存在。

- [ ] **Step 3: 实现限定路径的删除服务**

```python
def delete_run(session: Session, run_id: int, *, data_dir: Path) -> tuple[int, int] | None:
    run = session.get(UpdateRun, run_id)
    if run is None or run.status == RUN_PROCESSING:
        return None
    runs_root = (data_dir / "runs").resolve()
    artifact_paths = [Path(file.storage_path).resolve() for file in run.files]
    if run.output_path:
        artifact_paths.append(Path(run.output_path).resolve())
    managed_paths = [path for path in artifact_paths if path.is_relative_to(runs_root)]
    file_count = sum(path.exists() for path in managed_paths)
    item_count = len(run.items)
    item_ids = [str(item.id) for item in run.items]
    session.query(AuditLog).filter(
        ((AuditLog.object_type == "update_run") & (AuditLog.object_id == str(run.id)))
        | ((AuditLog.object_type == "run_item") & AuditLog.object_id.in_(item_ids))
    ).delete(synchronize_session=False)
    session.delete(run)
    session.commit()
    for path in managed_paths:
        path.unlink(missing_ok=True)
    for directory in {path.parent for path in managed_paths}:
        if directory.parent == runs_root:
            shutil.rmtree(directory, ignore_errors=True)
    return item_count, file_count
```

`app/main.py` 的 `POST /updates/{run_id}/delete` 必须先做 CSRF 校验，删除成功后写一条不含指标明细的 `AuditLog(action="delete", object_type="update_run")`，并在 `/updates?notice=...` 显示一次性提示。`POST /admin/users/{user_id}/delete` 仅允许管理员删除其他账号且不得删除最后一个管理员；删除前把关联的 run/audit 外键设为 `None`，再删除用户并记审计。删除服务不删除数据目录外的任何路径，并把这种路径从文件计数中排除。

- [ ] **Step 4: 运行删除测试确认通过**

Run: `/Users/kale/Documents/熊总/.venv/bin/pytest tests/e2e/test_lan_flow.py -k 'deleting_completed_run or deleting_run_does_not_remove_path_outside_data_directory or deleting_processing_run or cannot_delete_self_or_last_admin or deletes_other_user' -q`

Expected: PASS。

- [ ] **Step 5: 提交安全删除能力**

```bash
git add app/jobs/service.py app/main.py tests/e2e/test_lan_flow.py
git commit -m "feat: add safe run and user deletion"
```

### Task 3: OCR 确认统计和低置信度非阻塞分流

**Files:**
- Modify: `app/jobs/processor.py:191-202`
- Modify: `app/main.py:466-493`
- Modify: `app/templates/preview.html:8-15`
- Test: `tests/integration/test_jobs.py`
- Test: `tests/e2e/test_lan_flow.py`

- [ ] **Step 1: 先写失败的 OCR 状态和预览展示测试**

```python
def test_image_row_status_keeps_high_coverage_low_confidence_row_nonblocking() -> None:
    metrics = {metric: Decimal("0.01") for metric in ALL_METRICS[:9]}
    row = OCRMetricRow("产品A", None, metrics, confidence=0.40)
    assert _image_row_status(row, {"annual_2025", "sharpe", "max_drawdown"}) == (
        "partial", "本次未识别：2025（%）, 近一年夏普比, 近一年最大回撤（%）；OCR 置信度较低"
    )

def test_preview_counts_values_and_confirmed_source_blanks(tmp_path: Path) -> None:
    client, factory = signed_in_client(tmp_path)
    session = factory()
    try:
        admin = session.scalar(select(User).where(User.username == "admin"))
        run = UpdateRun(operator_id=admin.id, cutoff_date=date(2026, 7, 17), status="completed")
        session.add(run)
        session.flush()
        statuses = {metric: "extracted" for metric in ALL_METRICS[:8]}
        statuses.update({metric: "source_blank" for metric in ALL_METRICS[8:11]})
        statuses["max_drawdown"] = "stale"
        session.add(RunItem(run_id=run.id, excel_row=2, row_status="partial", metric_values={metric: "0.01" for metric in ALL_METRICS[:8]}, metric_status=statuses, error_reason="本次未识别：annual_2023"))
        session.commit()
        run_id = run.id
    finally:
        session.close()
    preview = client.get(f"/updates/{run_id}/preview")
    assert "已确认 11 / 12 项（8 数值＋3 空值）" in preview.text
    assert "2023（%）" in preview.text
    assert "annual_2023" not in preview.text
```

- [ ] **Step 2: 运行目标测试确认失败**

Run: `/Users/kale/Documents/熊总/.venv/bin/pytest tests/integration/test_jobs.py -k high_coverage_low_confidence tests/e2e/test_lan_flow.py -k counts_values_and_confirmed_source_blanks -q`

Expected: FAIL，因为低置信度目前直接返回 `needs_review`，模板只计数 `metric_values`。

- [ ] **Step 3: 最小化实现状态与展示模型**

```python
def _image_row_status(row: OCRMetricRow, missing_metrics: set[str]) -> tuple[str, str | None]:
    confirmed_count = len(ALL_METRICS) - len(missing_metrics)
    if not missing_metrics:
        return "ready", "OCR 置信度较低" if row.confidence < 0.85 else None
    message = f"本次未识别：{_metric_labels(missing_metrics)}"
    if confirmed_count >= PARTIAL_MINIMUM_CONFIRMED_FIELDS:
        if row.confidence < 0.85:
            message += "；OCR 置信度较低"
        return "partial", message
    return "needs_review", message
```

在 `preview_update` 中创建每行 `confirmed_count`、`value_count`、`source_blank_count` 与中文 `error_reason` 的展示对象；模板输出 `已确认 {{ confirmed_count }} / {{ metric_count }} 项（{{ value_count }} 数值＋{{ source_blank_count }} 空值）`。`partial` 不加入 `REVIEWABLE_STATUSES`。

- [ ] **Step 4: 运行 OCR 和预览测试确认通过**

Run: `/Users/kale/Documents/熊总/.venv/bin/pytest tests/integration/test_jobs.py -k 'partial or source_blank or high_coverage_low_confidence' tests/e2e/test_lan_flow.py -k counts_values_and_confirmed_source_blanks -q`

Expected: PASS。

- [ ] **Step 5: 提交 OCR 可用性修复**

```bash
git add app/jobs/processor.py app/main.py app/templates/preview.html tests/integration/test_jobs.py tests/e2e/test_lan_flow.py
git commit -m "fix: clarify OCR confirmation coverage"
```

### Task 4: 侧边栏应用壳和页面操作入口

**Files:**
- Modify: `app/templates/base.html:1-25`
- Modify: `app/templates/updates.html:1-7`
- Modify: `app/templates/admin_users.html:1-5`
- Modify: `app/static/app.css:1-80, 180-220`
- Test: `tests/e2e/test_lan_flow.py`

- [ ] **Step 1: 写入失败的页面结构测试**

```python
def test_workspace_pages_render_sidebar_and_safe_delete_controls(tmp_path: Path) -> None:
    client, factory = signed_in_client(tmp_path)
    completed_run_with_artifacts(factory, tmp_path)
    session = factory()
    try:
        session.add(User(username="operator", password_hash=hash_password("password-1"), role="user"))
        session.commit()
    finally:
        session.close()
    updates = client.get("/updates")
    users = client.get("/admin/users")
    assert 'class="app-sidebar"' in updates.text
    assert 'class="nav-link is-active"' in updates.text
    assert 'action="/updates/1/delete"' in updates.text
    assert "确定永久删除此批次及其上传文件和结果文件吗？" in updates.text
    assert 'action="/admin/users/' in users.text
    assert "当前登录账号，不能删除" in users.text
    assert ".app-sidebar" in client.get("/static/app.css").text
    assert "@media (max-width: 799px)" in client.get("/static/app.css").text
```

- [ ] **Step 2: 运行测试确认失败**

Run: `/Users/kale/Documents/熊总/.venv/bin/pytest tests/e2e/test_lan_flow.py::test_workspace_pages_render_sidebar_and_safe_delete_controls -q`

Expected: FAIL，因为当前布局是 `.topbar`，没有删除控件。

- [ ] **Step 3: 实现响应式模板和 CSS**

`base.html` 使用 `<aside class="app-sidebar">`，主内容使用 `<main class="app-main">`；导航链接根据 `request.url.path` 添加 `is-active`，底部显示用户名与退出表单。`updates.html` 增加“创建账号”列，使用 `run.operator.username if run.operator else '已删除账号'` 展示保留下来的历史，并在每行以提交表单渲染删除按钮，带 `onsubmit="return confirm('确定永久删除此批次及其上传文件和结果文件吗？')"`。`admin_users.html` 不可删除时显示具体说明，其他账号显示同样受 CSRF 保护的删除表单。CSS 在 `min-width: 800px` 固定 232px 左侧栏、取消表格页的 1120px 最大宽度；`max-width: 799px` 改为顶部紧凑且横向滚动的导航。

- [ ] **Step 4: 运行页面测试确认通过**

Run: `/Users/kale/Documents/熊总/.venv/bin/pytest tests/e2e/test_lan_flow.py::test_workspace_pages_render_sidebar_and_safe_delete_controls -q`

Expected: PASS。

- [ ] **Step 5: 提交工作台界面**

```bash
git add app/templates/base.html app/templates/updates.html app/templates/admin_users.html app/templates/preview.html app/static/app.css tests/e2e/test_lan_flow.py
git commit -m "feat: add responsive workspace navigation"
```

### Task 5: 迁移、质量与部署验证

**Files:**
- Modify: `README.md`（仅在现有部署文档需要说明迁移与删除语义时）
- Test: `tests/`

- [ ] **Step 1: 在新的 SQLite 数据库验证迁移链**

Run: `DATABASE_URL=sqlite+pysqlite:///$(mktemp -d)/migration.db /Users/kale/Documents/熊总/.venv/bin/alembic upgrade head`

Expected: `0003_allow_deleted_user_history` 完成，`update_runs.operator_id` 可空。

- [ ] **Step 2: 运行完整自动测试与静态检查**

Run: `/Users/kale/Documents/熊总/.venv/bin/pytest -q && /Users/kale/Documents/熊总/.venv/bin/ruff check app tests`

Expected: 全部测试通过，Ruff 无问题。

- [ ] **Step 3: 审阅改动与提交最终文档**

Run: `git diff main...HEAD --check && git status --short`

Expected: 无空白错误；只包含本计划列出的文件。

- [ ] **Step 4: 部署到 Unraid 并探活**

Run: `rsync -az --delete --exclude '.git' --exclude '.worktrees' ./ root@192.168.5.28:/mnt/user/appdata/nav-updater/ && ssh root@192.168.5.28 'cd /mnt/user/appdata/nav-updater && docker compose up -d --build && curl -fsS http://127.0.0.1:8080/healthz'`

Expected: 构建完成且返回 `{"status":"ok"}`。浏览器访问 `http://192.168.5.28:8080`，确认侧边栏、删除控件、预览状态均可用。

- [ ] **Step 5: 提交最终验证文档**

```bash
git add README.md docs/superpowers/plans/2026-07-21-workspace-actions-layout.md
git commit -m "docs: document workspace management workflow"
```
