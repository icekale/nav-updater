# 私募产品监控总览 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 提供全中文私募产品最新业绩、数据缺失、更新过期监控，并支持导出当前筛选结果。

**Architecture:** 新建 `app/monitoring.py`，从 `Product`、`RunItem` 和已完成的 `UpdateRun` 计算每产品最新记录与异常。页面和导出共用一个查询服务；不新增数据库表或外部服务。

**Tech Stack:** Python 3.12、FastAPI、SQLAlchemy 2、Jinja2、openpyxl、pytest、Ruff。

---

## 文件职责

- `app/monitoring.py`：查询、状态、筛选和 Excel 字节生成。
- `app/main.py`：登录保护的监控和下载路由。
- `app/templates/monitoring.html`：中文概览、异常清单、表格和筛选表单。
- `app/templates/base.html`、`app/static/app.css`：导航和响应式样式。
- `tests/unit/test_monitoring.py`：查询、筛选和 Excel 测试。
- `tests/e2e/test_lan_flow.py`：页面、权限、跳转和下载测试。

### Task 1: 建立监控查询服务

**Files:**

- Create: `app/monitoring.py`
- Create: `tests/unit/test_monitoring.py`

- [ ] **Step 1: 写失败的最新记录和状态测试**

在 `tests/unit/test_monitoring.py` 用 SQLite 内存库建立启用私募 `正常产品`、`从未更新产品`、`缺失产品`、`过期产品`、`空值确认产品`，以及一个公募和一个停用私募。正常产品有两条完成批次；缺失产品最新条目为 `row_status="partial"`、`metric_status={"weekly": "stale"}`；过期产品最新截止日为 11 天前；空值确认产品状态只有 `source_blank`。

```python
dashboard = build_monitoring_dashboard(session, today=date(2026, 7, 21))
rows = {row.product_name: row for row in dashboard.rows}

assert set(rows) == {"正常产品", "从未更新产品", "缺失产品", "过期产品", "空值确认产品"}
assert rows["正常产品"].cutoff_date == date(2026, 7, 18)
assert rows["正常产品"].status == "normal"
assert rows["从未更新产品"].status == "never_updated"
assert rows["缺失产品"].status == "missing_data"
assert rows["过期产品"].status == "outdated"
assert rows["空值确认产品"].status == "normal"
assert dashboard.total_count == 5
assert dashboard.never_updated_count == 1
assert dashboard.missing_data_count == 1
assert dashboard.outdated_count == 1
```

- [ ] **Step 2: 运行测试，确认服务不存在**

Run: `/Users/kale/Documents/熊总/.venv/bin/pytest tests/unit/test_monitoring.py -q`

Expected: FAIL，`app.monitoring` 不存在。

- [ ] **Step 3: 定义数据模型和状态计算**

在 `app/monitoring.py` 定义以下常量和 dataclass：

```python
COMPLETED_RUN_STATUSES = {"completed", "completed_with_warnings"}
MISSING_ITEM_STATUSES = {"needs_review", "stale", "failed"}
MISSING_METRIC_STATUSES = {"stale", "failed", "insufficient_data"}
STATUS_LABELS = {
    "normal": "正常",
    "never_updated": "从未更新",
    "missing_data": "数据缺失",
    "outdated": "更新过期",
}

@dataclass(frozen=True)
class MonitoringRow:
    product_id: int
    product_name: str
    product_code: str
    cutoff_date: date | None
    processed_at: datetime | None
    metric_values: Mapping[str, Decimal | None]
    missing_metrics: tuple[str, ...]
    status: str
    status_label: str
    reason: str
    run_id: int | None
    run_item_id: int | None

@dataclass(frozen=True)
class MonitoringDashboard:
    rows: tuple[MonitoringRow, ...]
    exceptions: tuple[MonitoringRow, ...]
    total_count: int
    normal_count: int
    never_updated_count: int
    missing_data_count: int
    outdated_count: int
```

实现 `build_monitoring_dashboard(session, *, today: date | None = None, search: str = "", status_filter: str = "all")`。该函数查询启用私募产品，查询连接 `UpdateRun` 且状态在 `COMPLETED_RUN_STATUSES` 的 `RunItem`，并按 `(run.cutoff_date, run.created_at, item.id)` 为每个产品选择最大记录，`processed_at` 取 `run.finished_at or run.created_at`。JSON 指标转换为有限 `Decimal`；状态依次判定从未更新、条目/指标缺失、`cutoff_date < today - timedelta(days=10)`、正常。`source_blank` 不计入缺失。搜索匹配产品名称或代码，状态过滤仅接受 `STATUS_LABELS` 的键；未知值视为 `all`。顶部计数始终基于全部启用私募，搜索和状态筛选只作用于全产品表、异常清单和导出。行与异常分别按“从未更新、数据缺失、更新过期、正常”和最早截止日排序。

- [ ] **Step 4: 运行测试确认通过**

Run: `/Users/kale/Documents/熊总/.venv/bin/pytest tests/unit/test_monitoring.py -q`

Expected: PASS。

- [ ] **Step 5: 写失败的搜索和状态筛选测试**

在相同测试文件加入：

```python
missing = build_monitoring_dashboard(
    session, today=date(2026, 7, 21), search="缺失", status_filter="missing_data"
)
assert [row.product_name for row in missing.rows] == ["缺失产品"]
assert [row.product_name for row in missing.exceptions] == ["缺失产品"]

normal = build_monitoring_dashboard(
    session, today=date(2026, 7, 21), status_filter="normal"
)
assert {row.status for row in normal.rows} == {"normal"}
assert not normal.exceptions
```

- [ ] **Step 6: 运行筛选测试并提交**

Run: `/Users/kale/Documents/熊总/.venv/bin/pytest tests/unit/test_monitoring.py -q`

Expected: PASS。

Commit: `git add app/monitoring.py tests/unit/test_monitoring.py && git commit -m "feat: summarize private product monitoring"`

### Task 2: 生成中文监控 Excel

**Files:**

- Modify: `app/monitoring.py`
- Test: `tests/unit/test_monitoring.py`

- [ ] **Step 1: 写失败的 Excel 内容测试**

调用 `monitoring_workbook_bytes(dashboard.rows)`，再用 `openpyxl.load_workbook(BytesIO(...), data_only=True)` 检查：

```python
assert [cell.value for cell in sheet[1]][:6] == [
    "产品代码", "产品名称", "数据状态", "异常说明", "最近净值截止日", "最近处理时间"
]
assert sheet.max_row == len(dashboard.rows) + 1
assert sheet[2][0].value == dashboard.rows[0].product_code
assert "近一周（%）" in [cell.value for cell in sheet[1]]
assert "近一年最大回撤（%）" in [cell.value for cell in sheet[1]]
```

- [ ] **Step 2: 运行测试，确认函数不存在**

Run: `/Users/kale/Documents/熊总/.venv/bin/pytest tests/unit/test_monitoring.py -k workbook -q`

Expected: FAIL，`monitoring_workbook_bytes` 未定义。

- [ ] **Step 3: 实现 Excel 生成函数**

在 `app/monitoring.py` 用 `openpyxl.Workbook`、`BytesIO` 和 `METRIC_FIELDS` 实现：

```python
def monitoring_workbook_bytes(rows: Iterable[MonitoringRow]) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "私募产品监控"
    sheet.append([
        "产品代码", "产品名称", "数据状态", "异常说明", "最近净值截止日", "最近处理时间",
        *[field.label for field in METRIC_FIELDS], "缺失指标",
    ])
    for row in rows:
        sheet.append([
            row.product_code, row.product_name, row.status_label, row.reason,
            row.cutoff_date.isoformat() if row.cutoff_date else "",
            row.processed_at.strftime("%Y-%m-%d %H:%M") if row.processed_at else "",
            *[_export_metric(row.metric_values.get(field.name), field.is_percent) for field in METRIC_FIELDS],
            "、".join(row.missing_metrics),
        ])
    output = BytesIO()
    workbook.save(output)
    return output.getvalue()
```

`_export_metric` 对百分比值乘以 `Decimal("100")`，对夏普返回原值，对空值返回 `None`。冻结首行、加粗表头、为百分比指标设置 `0.00` 数字格式；不写入图片、审核说明或 OCR 原始文本。

- [ ] **Step 4: 运行 Excel 测试并提交**

Run: `/Users/kale/Documents/熊总/.venv/bin/pytest tests/unit/test_monitoring.py -k workbook -q`

Expected: PASS。

Commit: `git add app/monitoring.py tests/unit/test_monitoring.py && git commit -m "feat: export private product monitoring"`

### Task 3: 添加中文页面、跳转和下载路由

**Files:**

- Modify: `app/main.py:1-60, 225-250`
- Modify: `app/templates/base.html:14-24`
- Create: `app/templates/monitoring.html`
- Modify: `app/static/app.css`
- Test: `tests/e2e/test_lan_flow.py`

- [ ] **Step 1: 写失败的页面、权限和导出测试**

在 `tests/e2e/test_lan_flow.py` 创建启用私募 `监控产品`、一条 `completed_with_warnings` 批次和 `RunItem(row_status="stale", metric_status={"weekly": "stale"})`。登录后访问 `/monitoring?status=missing_data`，断言：

```python
assert response.status_code == 200
assert "产品监控" in response.text
assert "数据缺失" in response.text
assert "导出当前清单" in response.text
assert f'/updates/{run_id}/review?show_all=1#review-item-{item_id}' in response.text
```

未登录客户端访问 `/monitoring` 与 `/monitoring/export.xlsx` 均为 `303` 且跳转 `/login`。登录客户端访问 `/monitoring/export.xlsx?status=missing_data`，断言 Excel MIME 类型、`Content-Disposition` 含 `filename*=`，对文件名 URL 解码后包含“私募产品监控”，并用 openpyxl 读取到一条数据行。

- [ ] **Step 2: 运行测试，确认路由不存在**

Run: `/Users/kale/Documents/熊总/.venv/bin/pytest tests/e2e/test_lan_flow.py -k private_product_monitoring -q`

Expected: FAIL，`/monitoring` 尚未定义。

- [ ] **Step 3: 实现路由和模板**

在 `app/main.py` 从 `fastapi.responses` 导入 `Response`，从 `app.monitoring` 导入 `STATUS_LABELS`、`build_monitoring_dashboard`、`monitoring_workbook_bytes`。实现受 `current_user` 保护的路由：

```python
@app.get("/monitoring", response_class=HTMLResponse)
def monitoring_page(..., search: str = "", status_filter: str = "all", ...):
    dashboard = build_monitoring_dashboard(session, search=search, status_filter=status_filter)
    return templates.TemplateResponse(
        request=request,
        name="monitoring.html",
        context={"user": user, "dashboard": dashboard, "search": search,
                 "status_filter": status_filter, "status_labels": STATUS_LABELS,
                 "csrf_token": csrf_token(request)},
    )

@app.get("/monitoring/export.xlsx")
def monitoring_export(..., search: str = "", status_filter: str = "all", ...):
    dashboard = build_monitoring_dashboard(session, search=search, status_filter=status_filter)
    return Response(
        monitoring_workbook_bytes(dashboard.rows),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename*=UTF-8''%E7%A7%81%E5%8B%9F%E4%BA%A7%E5%93%81%E7%9B%91%E6%8E%A7.xlsx"},
    )
```

在 `base.html` 的“质检中心”后增加“产品监控”链接和激活状态。`monitoring.html` 用 GET 表单保留 `search` 与 `status_filter`，导出链接使用相同参数；显示五个统计块、异常清单和全产品表。只有 `row.run_id` 存在时显示“查看批次”；只有 `row.status == "missing_data"` 且 `row.run_item_id` 存在时显示“去审核”，路径为 `/updates/{{ row.run_id }}/review?show_all=1#review-item-{{ row.run_item_id }}`。

在 CSS 添加 `.monitoring-kpis`、`.monitoring-kpi`、`.monitoring-filter`、`.monitoring-summary` 和移动端单列规则，复用现有表格、状态、按钮和侧边栏。

- [ ] **Step 4: 运行页面、权限和导出测试**

Run: `/Users/kale/Documents/熊总/.venv/bin/pytest tests/e2e/test_lan_flow.py -k private_product_monitoring -q`

Expected: PASS。

- [ ] **Step 5: 提交页面实现**

Commit: `git add app/main.py app/templates/base.html app/templates/monitoring.html app/static/app.css tests/e2e/test_lan_flow.py && git commit -m "feat: add private product monitoring page"`

### Task 4: 全量验证、审阅、合并和部署

**Files:**

- Modify: `docs/superpowers/plans/2026-07-21-private-product-monitor.md`
- Test: `tests/`

- [ ] **Step 1: 运行完整验证**

Run: `/Users/kale/Documents/熊总/.venv/bin/pytest -q && /Users/kale/Documents/熊总/.venv/bin/ruff check app tests migrations && git diff main...HEAD --check`

Expected: pytest 全部通过、Ruff 无问题、diff 无空白错误。

- [ ] **Step 2: 审阅改动范围**

Run: `git diff --stat main...HEAD`

Expected: 仅包含监控服务、导出、路由、中文模板、样式、测试和本计划。

- [ ] **Step 3: 提交实施计划**

Commit: `git add docs/superpowers/plans/2026-07-21-private-product-monitor.md && git commit -m "docs: add private product monitoring plan"`

- [ ] **Step 4: 合并并部署到 Unraid**

在主工作区快进合并功能分支，重新验证并推送 `main`。同步到 `/mnt/user/appdata/nav-updater/` 时保留 `.env`，以 `nav-updater-app:latest` 为基底快速构建；将新镜像标记为 `nav-updater-app:latest` 和 `nav-updater-worker:latest`，再用 `docker compose up -d --no-build --force-recreate` 启动。

Run: `ssh root@192.168.5.28 'cd /mnt/user/appdata/nav-updater && docker compose ps && curl -fsS http://127.0.0.1:8080/healthz'`

Expected: `app`、`worker`、`db` 均运行，健康端点返回 `{"status":"ok"}`，未登录监控页面跳转登录页。
