# RapidOCR 减少人工审核 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让唯一匹配且确认字段足够的 RapidOCR 截图行不再阻塞人工审核，同时保留空值与名称歧义的安全边界。

**Architecture:** 在 RapidOCR 原始 token 之外，增加仅针对孤立短横线的视觉 token，供既有表格解析器归类为 `source_blank`。产品名匹配继续限制在当前 Excel 与已启用目录中；当识别行确认至少 9 个字段时，处理器写入已确认字段、保留其余单元格并赋予新的非阻塞 `partial` 状态。

**Tech Stack:** Python 3.12、FastAPI/Jinja、SQLAlchemy、RapidOCR、OpenCV、lxml、pytest、Ruff。

---

## File Structure

- `app/ocr/engine.py`：检测 OCR 漏掉的孤立短横线。
- `app/ocr/benchmark.py`：单独汇总已确认源空值的识别率。
- `app/domain/matching.py`：标准化 OCR 名称中的确定展示噪声。
- `app/jobs/processor.py`：受限产品匹配和 `partial` 状态分流。
- `app/main.py`、`app/templates/preview.html`、`app/templates/review.html`、`app/static/app.css`：展示非阻塞缺失字段。
- `tests/unit/test_ocr_engine.py`、`tests/unit/test_matching.py`、`tests/unit/test_table_parser.py`：底层回归测试。
- `tests/integration/test_jobs.py`、`tests/e2e/test_lan_flow.py`：批次、Excel 与页面回归测试。
- `README.md`、`benchmarks/ocr/README.md`：基准验收说明。

### Task 1: 从图像 tile 保留明确的短横线空值

**Files:**
- Modify: `app/ocr/engine.py`
- Modify: `tests/unit/test_ocr_engine.py`
- Modify: `tests/unit/test_table_parser.py`

- [ ] **Step 1: 写入失败测试**

在 `tests/unit/test_ocr_engine.py` 中创建白色数组，用 `cv2.line()` 画出一个宽 17 像素、高 2 像素的黑色短横线；断言新 helper 生成一个 `OCRToken("-")`。再给同一位置一个既有 `OCRToken("-12.95%")`，断言不生成空值 token。

```python
image = np.full((80, 200, 3), 255, dtype=np.uint8)
cv2.line(image, (110, 42), (126, 42), (0, 0, 0), 2)
assert [item.text for item in _detect_source_blank_tokens(image, [])] == ["-"]
```

在 `tests/unit/test_table_parser.py` 中将这个 `-` token 放到 `MTD(%)` 表头下，断言 `rows[0].blank_metrics == frozenset({"mtd"})` 且 `mtd` 不在 `metrics` 中。

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/bin/pytest tests/unit/test_ocr_engine.py tests/unit/test_table_parser.py -q`

Expected: 因 `_detect_source_blank_tokens` 尚不存在而失败。

- [ ] **Step 3: 实现最小检测器**

在 `app/ocr/engine.py` 中让 `OCRService.recognize()` 先获得 `source = _load_image(image)`，将 `source` 送入 RapidOCR，再把 helper 结果追加到原有 token。新增 helper 只接受宽度 6–80、高度 1–6、宽高比至少 3 的二值化连通域；若连通域与任一既有 token 的边界框（四像素扩展）相交则忽略。

```python
def _detect_source_blank_tokens(image: np.ndarray, existing: list[OCRToken]) -> list[OCRToken]:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    _, binary = cv2.threshold(gray, 100, 255, cv2.THRESH_BINARY_INV)
    count, _, stats, _ = cv2.connectedComponentsWithStats(binary, connectivity=8)
    blanks = []
    for left, top, width, height, _ in stats[1:count]:
        if not (6 <= width <= 80 and 1 <= height <= 6 and width >= height * 3):
            continue
        if _overlaps_recognized_text(left, top, width, height, existing):
            continue
        blanks.append(OCRToken("-", ((left, top), (left + width, top), (left + width, top + height), (left, top + height)), 1.0))
    return blanks


def _overlaps_recognized_text(left: int, top: int, width: int, height: int, existing: list[OCRToken]) -> bool:
    return any(
        left < token.left + 4 and left + width > token.left - 4
        and top < token.top + 4 and top + height > token.top - 4
        for token in existing
    )
```

保留 `recognize_tiled()` 的现有坐标偏移和去重路径，使单图和长图使用同一实现。

- [ ] **Step 4: 验证通过**

Run:

```bash
.venv/bin/pytest tests/unit/test_ocr_engine.py tests/unit/test_table_parser.py -q
.venv/bin/ruff check app/ocr/engine.py tests/unit/test_ocr_engine.py tests/unit/test_table_parser.py
```

Expected: 选定测试全部通过，Ruff 无错误。

- [ ] **Step 5: 提交**

```bash
git add app/ocr/engine.py tests/unit/test_ocr_engine.py tests/unit/test_table_parser.py
git commit -m "fix: retain RapidOCR source blank markers"
```

### Task 2: 仅接受唯一的 OCR 产品名候选

**Files:**
- Modify: `app/domain/matching.py`
- Modify: `app/jobs/processor.py`
- Modify: `tests/unit/test_matching.py`
- Modify: `tests/integration/test_jobs.py`

- [ ] **Step 1: 写入失败测试**

新增 `normalize_ocr_name()` 测试：`聚鸣金选高山8号B1]` 仅移除孤立的 `]`，而 `产品(稳健)` 保留完整括号。新增 `_find_image_row()` 测试：当 OCR 名 `仁桥金选泽源5B1]` 的首段汉字在本批 Excel 中只对应 `仁桥金选泽源5B` 时可匹配；当 Excel 同时有 `聚鸣金选高山8号B` 和 `聚鸣金选高山3号B` 时，同样的前缀必须拒绝。

```python
assert normalize_ocr_name("聚鸣金选高山8号B1]") == "聚鸣金选高山8号b1"
assert _find_image_row("仁桥金选泽源5B", [row], [], ["仁桥金选泽源5B"]) == row
assert _find_image_row("聚鸣金选高山8号B", [row], [], ["聚鸣金选高山8号B", "聚鸣金选高山3号B"]) is None
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/bin/pytest tests/unit/test_matching.py tests/integration/test_jobs.py -q`

Expected: 新 normalizer 不存在，当前规则拒绝等长中文前缀。

- [ ] **Step 3: 实现受限候选规则**

在 `app/domain/matching.py` 新增只供 OCR 输入使用的函数：

```python
def normalize_ocr_name(value: str) -> str:
    text = value.strip().replace("（", "(").replace("）", ")")
    text = re.sub(r"\s+", "", text)
    text = re.sub(r"[\]\}]+$", "", text)
    return text.casefold()
```

在 `app/jobs/processor.py` 保持代码、目录标准名和历史名称精确匹配优先。其后以 OCR 名开头至少四个汉字建立 Excel 候选集合；候选的标准化名称集合仅等于当前 item 名时才返回该行。候选为零或多个时返回 `None`。不得删除产品数字、份额标识、管理人名称，也不得使用编辑距离。

- [ ] **Step 4: 验证通过**

Run:

```bash
.venv/bin/pytest tests/unit/test_matching.py tests/integration/test_jobs.py -q
.venv/bin/ruff check app/domain/matching.py app/jobs/processor.py tests/unit/test_matching.py tests/integration/test_jobs.py
```

Expected: 唯一候选通过，歧义候选仍进入人工审核。

- [ ] **Step 5: 提交**

```bash
git add app/domain/matching.py app/jobs/processor.py tests/unit/test_matching.py tests/integration/test_jobs.py
git commit -m "fix: match unique OCR product name candidates"
```

### Task 3: 为高覆盖截图行引入非阻塞 `partial` 状态

**Files:**
- Modify: `app/jobs/processor.py`
- Modify: `app/main.py`
- Modify: `app/templates/preview.html`
- Modify: `app/templates/review.html`
- Modify: `app/static/app.css`
- Modify: `tests/integration/test_jobs.py`
- Modify: `tests/e2e/test_lan_flow.py`

- [ ] **Step 1: 写入失败测试**

将现有部分 OCR 指标测试扩展为 9 个确认字段、3 个缺失字段。断言行状态为 `partial`，三个字段状态为 `stale`，已识别字段进入 adapter 更新，缺失字段仅出现在 adapter 的 `stale` 集合。另加六个确认字段用例，断言仍为 `needs_review`。

```python
assert item.row_status == "partial"
assert adapter.updates[item.excel_row]["weekly"] == Decimal("0.052")
assert adapter.stale[item.excel_row] == {"annual_2019", "annual_2020", "annual_2021"}
```

在 `tests/e2e/test_lan_flow.py` 创建一条 `partial` item，断言预览包含 `部分识别`、`本次未识别` 与 `已识别 9 / 12 项`，默认待审核数字不包含它；`/review` 不显示它，`/review?show_all=1` 显示它。

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/bin/pytest tests/integration/test_jobs.py tests/e2e/test_lan_flow.py -q`

Expected: 当前所有缺字段截图行均被标记为 `needs_review`。

- [ ] **Step 3: 实现状态分流**

在 `app/jobs/processor.py` 定义 `PARTIAL_MINIMUM_CONFIRMED_FIELDS = 9` 与 helper：

```python
def _image_row_status(row: OCRMetricRow, missing_metrics: set[str]) -> tuple[str, str | None]:
    confirmed_count = len(ALL_METRICS) - len(missing_metrics)
    if row.confidence < 0.85:
        return "needs_review", "OCR confidence below threshold"
    if not missing_metrics:
        return "ready", None
    message = f"本次未识别：{', '.join(sorted(missing_metrics))}"
    if confirmed_count >= PARTIAL_MINIMUM_CONFIRMED_FIELDS:
        return "partial", message
    return "needs_review", message
```

用 helper 替换现有 image-row `review_reasons` 判断。继续将 `missing_metrics` 传给 `stale`，因此 `TemplateAdapter` 保留旧单元格并上错误样式。只有 `needs_review`、`stale`、`failed` 计作运行警告；`partial` 可下载但在预览中保留说明。

在 `app/main.py` 为 `partial` 添加标签 `部分识别`，不把它加入 `REVIEWABLE_STATUSES`。在 `preview.html` 显示 error reason；在 `review.html` 将 all-items 链接改为 `显示全部产品`。在 `app/static/app.css` 添加 `.status-partial` 的中性琥珀色样式。

- [ ] **Step 4: 验证通过**

Run:

```bash
.venv/bin/pytest tests/integration/test_jobs.py tests/e2e/test_lan_flow.py tests/test_workflow_layout.py -q
.venv/bin/ruff check app/jobs/processor.py app/main.py tests/integration/test_jobs.py tests/e2e/test_lan_flow.py
```

Expected: 9–11 个确认字段为 `partial`，默认审核数不含它，导出缺失字段仍高亮且未清空。

- [ ] **Step 5: 提交**

```bash
git add app/jobs/processor.py app/main.py app/templates/preview.html app/templates/review.html app/static/app.css tests/integration/test_jobs.py tests/e2e/test_lan_flow.py
git commit -m "feat: keep high-coverage OCR rows out of review backlog"
```

### Task 4: 固化 RapidOCR 质量门槛

**Files:**
- Modify: `app/ocr/benchmark.py`
- Modify: `README.md`
- Modify: `benchmarks/ocr/README.md`
- Modify: `tests/unit/test_ocr_benchmark.py`

- [ ] **Step 1: 写入源空值基准测试**

在 `tests/unit/test_ocr_benchmark.py` 使用一个数值字段和一个预期为 `None`、且出现在 `blank_metrics` 中的字段创建报告。断言 `report_as_dict(report)["totals"]` 的 `source_blanks` 为 1、`correct_source_blanks` 为 1、`source_blank_recall` 为 `1.0`；断言 Markdown 含有 `源空值识别率`。

```python
totals = report_as_dict(report)["totals"]
assert totals["source_blank_recall"] == 1.0
assert "源空值识别率" in render_markdown(report)
```

- [ ] **Step 2: 运行测试确认当前报告格式**

Run: `.venv/bin/pytest tests/unit/test_ocr_benchmark.py -q`

Expected: failure because the report does not yet expose source-blank totals.

- [ ] **Step 3: 实现源空值汇总并写入操作文档**

在 `app/ocr/benchmark.py` 为 `BenchmarkCaseResult` 增加带默认值的字段，避免现有单元测试构造器失效：

```python
expected_blank_metrics: frozenset[str] = frozenset()
```

在 `evaluate_cases()` 创建每个结果时填入：

```python
expected_blank_metrics=frozenset(
    metric for metric, expected in case.metrics.items() if expected is None
)
```

在 `BenchmarkReport` 中实现：

```python
@property
def source_blanks(self) -> int:
    return sum(len(result.expected_blank_metrics) for result in self.results)

@property
def correct_source_blanks(self) -> int:
    return sum(
        result.field_outcomes.get(metric) == "correct"
        for result in self.results
        for metric in result.expected_blank_metrics
    )

@property
def source_blank_recall(self) -> float:
    return _rate(self.correct_source_blanks, self.source_blanks)
```

在 `report_as_dict()` 和 `render_markdown()` 输出这三个值；保留现有字段准确率和错列计算语义不变。

随后在 `README.md` 写入操作文档：

在 `README.md` 上传流程后说明：RapidOCR 是生产默认；`source_blank` 会清空单元格；`partial` 更新确认字段、保留并标红未识别字段。添加命令：

```bash
.venv/bin/python scripts/run_ocr_benchmark.py --labels /secure/ocr-labels.json --images-root /secure/weekly-reports --output-dir /secure/ocr-results/2026-07-20
```

在 `benchmarks/ocr/README.md` 添加验收阈值：产品匹配率至少 90%，字段准确率至少 98%，源空值识别率至少 95%，错列数为 0，歧义名称不得自动写入。研究员标签与图片不加入 Git。

- [ ] **Step 4: 验证文档与测试**

Run:

```bash
.venv/bin/pytest tests/unit/test_ocr_benchmark.py -q
git diff --check
```

Expected: 测试通过且没有格式错误。没有研究员标签文件时，不宣称数值门槛已达标。

- [ ] **Step 5: 提交**

```bash
git add README.md benchmarks/ocr/README.md tests/unit/test_ocr_benchmark.py
git commit -m "docs: define RapidOCR quality gate"
```

### Task 5: 完整验证和仅对新批次部署

**Files:**
- Verify: `app/ocr/engine.py`
- Verify: `app/domain/matching.py`
- Verify: `app/jobs/processor.py`
- Verify: `app/main.py`
- Verify: `app/excel/template_adapter.py`

- [ ] **Step 1: 运行完整自动化验证**

Run:

```bash
.venv/bin/pytest -q
.venv/bin/ruff check .
```

Expected: 全部测试通过且 Ruff 无错误。

- [ ] **Step 2: 运行研究员标签基准**

Run Task 4 的基准命令，阅读 `summary.md` 与 `details.json`。若任一阈值未达到，停止部署并根据错误样本补测试；不要根据识别行数宣称准确率。

- [ ] **Step 3: 部署并验证默认引擎**

Run on Unraid:

```bash
cd /mnt/user/appdata/nav-updater
git pull --ff-only origin main
docker compose up -d --build
docker compose exec -T worker python -c 'from app.config import get_settings; from app.ocr.engine import create_ocr_service; print(get_settings().ocr_backend); print(type(create_ocr_service()).__name__)'
curl --fail http://127.0.0.1:8080/healthz
```

Expected: 输出 `rapid`、`OCRService` 和 `{"status":"ok"}`。

- [ ] **Step 4: 上传新的验证批次**

使用一个新的 Excel＋截图批次验证：完整行显示 `ready`；显示 `-` 的行清空对应字段；9–11 字段行显示 `partial` 且不计入默认审核；未匹配产品仍要求审核。不得重新排队历史批次。

- [ ] **Step 5: 提交仅因验证产生的文档变更**

若新批次验证改变了用户可见说明，更新 `README.md`，重新运行完整套件后执行：

```bash
git add README.md
git commit -m "docs: clarify RapidOCR batch verification"
```
