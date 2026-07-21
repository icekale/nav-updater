# 更新历史批量管理 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让研究员在当前更新历史页批量重新生成或永久删除多个批次，并保留现有单批次安全和审计语义。

**Architecture:** 在 `app/jobs/service.py` 增加轻量批量协调函数，逐个复用 `requeue_run` 与 `delete_run`，统计成功、处理中跳过和不存在数量。FastAPI 路由验证请求、调用服务并返回汇总通知；Jinja 模板通过表格复选框和小型 JavaScript 显示已选数量及操作栏。

**Tech Stack:** Python 3.12、FastAPI、SQLAlchemy、Jinja2、原生 JavaScript、pytest、Ruff。

---

## 文件职责

- `app/jobs/service.py`：集中管理选中批次的状态判断、重排队、删除和结果计数。
- `app/main.py`：接收受 CSRF 保护的批量表单，记录重新生成审计，并显示汇总通知。
- `app/templates/updates.html`：增加复选框、全选当前页、批量操作栏和删除确认。
- `app/static/app.css`：为批量操作栏和复选框列提供紧凑、响应式样式。
- `tests/e2e/test_lan_flow.py`：覆盖批量重排队、批量删除、处理中跳过和页面控件。

### Task 1: 批量服务协调

**Files:**
- Modify: `app/jobs/service.py:1-190`
- Test: `tests/e2e/test_lan_flow.py:100-220`

- [ ] **Step 1: 扩展文件批次测试夹具，并写失败的重排队测试**

将 `_create_run_with_artifacts` 改为接收 `directory: str = "run-artifacts"`，并让 `run_dir = data_dir / "runs" / directory`。加入：

```python
def test_batch_requeue_moves_each_completed_run_back_to_the_queue(tmp_path: Path) -> None:
    app, factory = _test_app(tmp_path)
    with TestClient(app) as client:
        _login_as_admin(client)
        first_id, _, _, _ = _create_run_with_artifacts(factory, tmp_path, directory="batch-a")
        second_id, _, _, _ = _create_run_with_artifacts(factory, tmp_path, directory="batch-b")
        response = client.post(
            "/updates/batch",
            data=[
                ("token", _token(client, "/updates")),
                ("action", "requeue"),
                ("run_ids", str(first_id)),
                ("run_ids", str(second_id)),
            ],
            follow_redirects=False,
        )
    assert response.status_code == 303
    session = factory()
    try:
        assert session.get(UpdateRun, first_id).status == "uploaded"
        assert session.get(UpdateRun, second_id).status == "uploaded"
        assert session.query(AuditLog).filter_by(action="queue", object_type="update_run").count() == 2
    finally:
        session.close()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `/Users/kale/Documents/熊总/.venv/bin/pytest tests/e2e/test_lan_flow.py::test_batch_requeue_moves_each_completed_run_back_to_the_queue -q`

Expected: FAIL，`POST /updates/batch` 尚未定义。

- [ ] **Step 3: 实现批量服务结果和逐批次协调**

在 `app/jobs/service.py` 导入 `replace` 与 `AuditLog`，并在 `delete_run` 前定义：

```python
@dataclass(frozen=True)
class BatchRunResult:
    requeued: int = 0
    deleted: int = 0
    skipped_processing: int = 0
    missing: int = 0


def batch_manage_runs(
    session: Session,
    run_ids: Iterable[int],
    *,
    action: str,
    data_dir: Path,
    actor_id: int,
) -> BatchRunResult:
    result = BatchRunResult()
    for run_id in dict.fromkeys(run_ids):
        run = session.get(UpdateRun, run_id)
        if run is None:
            result = replace(result, missing=result.missing + 1)
        elif run.status == RUN_PROCESSING:
            result = replace(result, skipped_processing=result.skipped_processing + 1)
        elif action == "requeue":
            requeue_run(session, run_id)
            session.add(
                AuditLog(
                    actor_id=actor_id,
                    action="queue",
                    object_type="update_run",
                    object_id=str(run_id),
                )
            )
            session.commit()
            result = replace(result, requeued=result.requeued + 1)
        elif action == "delete":
            delete_run(session, run_id, data_dir=data_dir, actor_id=actor_id)
            result = replace(result, deleted=result.deleted + 1)
        else:
            raise ValueError("unsupported batch action")
    return result
```

- [ ] **Step 4: 运行测试确认通过**

Run: `/Users/kale/Documents/熊总/.venv/bin/pytest tests/e2e/test_lan_flow.py::test_batch_requeue_moves_each_completed_run_back_to_the_queue -q`

Expected: PASS。

- [ ] **Step 5: 提交服务改动**

```bash
git add app/jobs/service.py tests/e2e/test_lan_flow.py
git commit -m "feat: add batch update run operations"
```

### Task 2: 批量路由和汇总通知

**Files:**
- Modify: `app/main.py:40-55, 201-255`
- Test: `tests/e2e/test_lan_flow.py:220-360`

- [ ] **Step 1: 写失败的删除、处理中跳过和无效请求测试**

```python
def test_batch_delete_preserves_processing_run_and_deletes_completed_runs(tmp_path: Path) -> None:
    app, factory = _test_app(tmp_path)
    with TestClient(app) as client:
        _login_as_admin(client)
        completed_id, _, completed_dir, _ = _create_run_with_artifacts(
            factory, tmp_path, directory="batch-delete"
        )
        processing_id, _, processing_dir, _ = _create_run_with_artifacts(
            factory, tmp_path, status="processing", directory="batch-processing"
        )
        response = client.post(
            "/updates/batch",
            data=[
                ("token", _token(client, "/updates")),
                ("action", "delete"),
                ("run_ids", str(completed_id)),
                ("run_ids", str(processing_id)),
            ],
            follow_redirects=False,
        )
        history = client.get(response.headers["location"])
    assert "已删除 1 个批次，跳过处理中 1 个" in history.text
    session = factory()
    try:
        assert session.get(UpdateRun, completed_id) is None
        assert session.get(UpdateRun, processing_id) is not None
    finally:
        session.close()
    assert not completed_dir.exists()
    assert processing_dir.exists()
```

增加 `test_batch_rejects_empty_or_unknown_action`：提交空 `run_ids` 或 `action="unknown"` 后，历史页分别显示“请选择至少一个批次”与“批量操作无效”，原批次状态不变。

- [ ] **Step 2: 运行测试确认失败**

Run: `/Users/kale/Documents/熊总/.venv/bin/pytest tests/e2e/test_lan_flow.py -k 'batch_delete_preserves_processing or batch_requeue_moves_each or batch_rejects' -q`

Expected: FAIL，因为路由尚未定义。

- [ ] **Step 3: 实现路由及通知**

从 `.jobs.service` 导入 `BatchRunResult` 与 `batch_manage_runs`。在 `delete_update` 前添加：

```python
def _batch_notice(result: BatchRunResult) -> str:
    messages = []
    if result.requeued:
        messages.append(f"已重新生成 {result.requeued} 个批次")
    if result.deleted:
        messages.append(f"已删除 {result.deleted} 个批次")
    if result.skipped_processing:
        messages.append(f"跳过处理中 {result.skipped_processing} 个")
    if result.missing:
        messages.append(f"不存在 {result.missing} 个")
    return "，".join(messages)


@app.post("/updates/batch")
def batch_update_runs(
    request: Request,
    token: str = Form(...),
    action: str = Form(...),
    run_ids: list[int] = Form(default=[]),
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    require_csrf(request, token)
    if not run_ids:
        return RedirectResponse("/updates?notice=请选择至少一个批次", status_code=303)
    if action not in {"requeue", "delete"}:
        return RedirectResponse("/updates?notice=批量操作无效", status_code=303)
    result = batch_manage_runs(
        session,
        run_ids,
        action=action,
        data_dir=ensure_data_dir(app.state.settings),
        actor_id=user.id,
    )
    return RedirectResponse(
        f"/updates?{urlencode({'notice': _batch_notice(result)})}",
        status_code=303,
    )
```

- [ ] **Step 4: 运行批量路由测试确认通过**

Run: `/Users/kale/Documents/熊总/.venv/bin/pytest tests/e2e/test_lan_flow.py -k 'batch_delete_preserves_processing or batch_requeue_moves_each or batch_rejects' -q`

Expected: PASS。

- [ ] **Step 5: 提交路由改动**

```bash
git add app/main.py tests/e2e/test_lan_flow.py
git commit -m "feat: add batch history management route"
```

### Task 3: 选择控件和响应式操作栏

**Files:**
- Modify: `app/templates/updates.html:1-45`
- Modify: `app/static/app.css:125-155, 260-275`
- Test: `tests/e2e/test_lan_flow.py:400-450`

- [ ] **Step 1: 写失败的页面控件测试**

```python
def test_history_page_renders_batch_controls_for_visible_runs(tmp_path: Path) -> None:
    app, factory = _test_app(tmp_path)
    with TestClient(app) as client:
        _login_as_admin(client)
        first_id, _, _, _ = _create_run_with_artifacts(factory, tmp_path, directory="batch-ui-a")
        second_id, _, _, _ = _create_run_with_artifacts(factory, tmp_path, directory="batch-ui-b")
        response = client.get("/updates")
        stylesheet = client.get("/static/app.css")
    assert 'id="batch-run-form"' in response.text
    assert 'id="select-all-runs"' in response.text
    assert f'name="run_ids" value="{first_id}"' in response.text
    assert f'name="run_ids" value="{second_id}"' in response.text
    assert 'name="action" value="requeue"' in response.text
    assert 'name="action" value="delete"' in response.text
    assert "确定永久删除所选批次及其上传文件和结果文件吗？" in response.text
    assert ".batch-toolbar" in stylesheet.text
```

- [ ] **Step 2: 运行测试确认失败**

Run: `/Users/kale/Documents/熊总/.venv/bin/pytest tests/e2e/test_lan_flow.py::test_history_page_renders_batch_controls_for_visible_runs -q`

Expected: FAIL，因为历史表没有复选框或批量表单。

- [ ] **Step 3: 实现批量表单和选择状态**

在 `updates.html` 中将操作栏和表格置于 `<form id="batch-run-form" method="post" action="/updates/batch">`，加入 CSRF token、`id="select-all-runs"` 表头复选框和每个批次的 `name="run_ids" value="{{ run.id }}"` 复选框。使用：

```html
<div id="batch-toolbar" class="batch-toolbar" hidden aria-live="polite">
  <span id="selected-run-count">已选 0 条</span>
  <button class="button" type="submit" name="action" value="requeue">重新生成结果</button>
  <button class="danger-button" type="submit" name="action" value="delete">永久删除</button>
</div>
```

在模板底部加入原生 JavaScript：监听 `[data-run-checkbox]` 的 `change`，同步全选框的 `checked`、`indeterminate`、操作栏 `hidden` 与已选数量；`submit` 时仅当 `event.submitter.value === "delete"` 才调用 `confirm("确定永久删除所选批次及其上传文件和结果文件吗？")`。

在 CSS 添加 `.batch-toolbar` 的水平弹性布局、灰白数据面和移动端纵向排列；复选框列宽固定，避免挤压批次与操作列。

- [ ] **Step 4: 运行页面测试确认通过**

Run: `/Users/kale/Documents/熊总/.venv/bin/pytest tests/e2e/test_lan_flow.py::test_history_page_renders_batch_controls_for_visible_runs -q`

Expected: PASS。

- [ ] **Step 5: 提交界面改动**

```bash
git add app/templates/updates.html app/static/app.css tests/e2e/test_lan_flow.py
git commit -m "feat: add batch controls to update history"
```

### Task 4: 验证、合并和部署

**Files:**
- Create: `docs/superpowers/plans/2026-07-21-batch-history-management.md`
- Test: `tests/`

- [ ] **Step 1: 运行完整测试和静态检查**

Run: `/Users/kale/Documents/熊总/.venv/bin/pytest -q && /Users/kale/Documents/熊总/.venv/bin/ruff check app tests migrations`

Expected: 全部测试通过，Ruff 无问题。

- [ ] **Step 2: 审阅改动范围**

Run: `git diff main...HEAD --check && git diff --stat main...HEAD`

Expected: 改动仅包含批量管理服务、路由、历史模板、样式、测试和本计划。

- [ ] **Step 3: 提交计划并合并**

```bash
git add docs/superpowers/plans/2026-07-21-batch-history-management.md
git commit -m "docs: add batch history implementation plan"
git checkout main
git merge --ff-only codex/batch-history-management
git push origin main
```

- [ ] **Step 4: 部署到 Unraid**

Run: `rsync -az --delete --exclude '.git' --exclude '.worktrees' --exclude '.venv' --exclude '.env' --exclude '__pycache__' ./ root@192.168.5.28:/mnt/user/appdata/nav-updater/ && ssh root@192.168.5.28 'cd /mnt/user/appdata/nav-updater && docker compose up -d --build && curl -fsS http://127.0.0.1:8080/healthz'`

Expected: 服务返回 `{"status":"ok"}`，更新历史出现复选框和批量操作栏。
