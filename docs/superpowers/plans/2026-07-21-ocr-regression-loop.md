# OCR Regression Quality Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将人工确认和批次 #12 的 OCR 案例沉淀为独立回归样本，给疑似空值增加二次识别，保存字段证据，并提供管理员可运行且不修改生产数据的回归验证。

**Architecture:** 保留现有 `OcrReviewSample` 作为人工审核审计快照；新增独立的回归样本、回归运行和运行结果模型，样本图片复制到 `app_data` 卷中，来源批次删除不影响样本。处理器首轮使用现有 1600px 分片；只对缺失、孤立破折号或无法归行的字段执行密集二次识别，并把最终选择及两轮原始证据写入 `RunItem.ocr_evidence`。质检中心继续读现有质量统计，并增加管理员专属的样本管理和后台回归任务入口。

**Tech Stack:** Python 3.12、FastAPI、SQLAlchemy 2、Alembic、RapidOCR、Jinja2、PostgreSQL/SQLite、pytest、Ruff、Docker Compose。

---

## 文件职责与边界

- `app/models.py`：增加 `RunItem.ocr_evidence`、`OcrRegressionSample`、`OcrRegressionRun`、`OcrRegressionResult`；样本来源外键使用 `SET NULL`，不随普通批次级联删除。
- `migrations/versions/0006_add_ocr_regression_loop.py`：创建回归表，并为 `run_items` 增加证据 JSON 列。
- `app/ocr/regression.py`：复制/校验样本图片、从审核快照导入样本、显式提升管理员案例、运行样本并比较结果；不写生产批次。
- `app/ocr/evidence.py`：把 OCR token、解析行和图片边界框转换成可序列化证据，并安全生成局部截图路径。
- `app/ocr/table_parser.py`：保留字段对应的原始 `ParsedCell`，让解析结果能携带字段边界框；现有解析结果和 benchmark 接口保持兼容。
- `app/jobs/processor.py`：组织首轮/二次识别、`source_blank` 判定和 `ocr_evidence` 写入。
- `app/jobs/regression_worker.py`：按回归运行 ID 执行样本，单个样本失败不影响其他样本。
- `app/jobs/worker.py`：轮询回归任务和更新批次任务；回归任务不能被普通批次 claim 逻辑误取。
- `app/main.py`：质量页的管理员操作、样本导入/提升、回归运行、证据图片访问和权限保护。
- `app/quality.py`：在现有人工审核统计之外聚合回归运行摘要、样本状态和失败清单。
- `app/templates/quality.html`：增加回归摘要、管理员操作和失败证据入口，普通用户只读。
- `app/templates/review.html`：在每个指标旁显示证据状态、原始文本和局部截图入口。
- `app/static/app.css`：只增加质量页与证据区所需的紧凑布局和状态样式。
- `tests/unit/test_ocr_regression.py`：覆盖样本去重、图片校验、结果比较和二次识别判定。
- `tests/unit/test_ocr_evidence.py`：覆盖 token/边界框 JSON 化和越界裁剪。
- `tests/integration/test_jobs.py`：覆盖二次识别、生产证据写入以及回归运行不改生产数据。
- `tests/e2e/test_lan_flow.py`：覆盖管理员/普通用户权限、样本导入、回归操作、质量页面和证据入口。
- `tests/test_migrations.py`：覆盖 0006 在 SQLite/模拟 PostgreSQL 操作下的升级与回滚结构。

## 任务 1：建立回归数据模型和迁移

**Files:**
- Modify: `app/models.py`
- Create: `migrations/versions/0006_add_ocr_regression_loop.py`
- Test: `tests/test_migrations.py`
- Test: `tests/unit/test_ocr_regression.py`

- [ ] **Step 1: 写模型结构的失败测试**

在 `tests/unit/test_ocr_regression.py` 添加如下导入和结构测试。基线中三个模型不存在，测试必须先失败：

```python
from datetime import datetime

from app.models import OcrRegressionResult, OcrRegressionRun, OcrRegressionSample, RunItem


def test_regression_models_keep_sample_when_source_run_is_deleted() -> None:
    assert OcrRegressionSample.__tablename__ == "ocr_regression_samples"
    assert OcrRegressionRun.__tablename__ == "ocr_regression_runs"
    assert OcrRegressionResult.__tablename__ == "ocr_regression_results"
    assert "ocr_evidence" in RunItem.__table__.c
    assert OcrRegressionSample.source_run_id.property.columns[0].nullable is True
    assert OcrRegressionSample.created_at.default is not None
```

- [ ] **Step 2: 运行失败测试确认基线**

Run: `../../.venv/bin/python -m pytest tests/unit/test_ocr_regression.py::test_regression_models_keep_sample_when_source_run_is_deleted -q`

Expected: FAIL，原因是回归模型和 `RunItem.ocr_evidence` 尚未定义。

- [ ] **Step 3: 增加最小 ORM 模型**

在 `RunItem` 增加：

```python
ocr_evidence: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
```

新增模型使用以下字段；`source_run_id` 与 `source_item_id` 为可空 `SET NULL` 外键，保证删除来源批次后样本仍可运行：

```python
class OcrRegressionSample(Base):
    __tablename__ = "ocr_regression_samples"

    id: Mapped[int] = mapped_column(primary_key=True)
    image_path: Mapped[str] = mapped_column(Text)
    image_sha256: Mapped[str] = mapped_column(String(64), index=True)
    source_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("update_runs.id", ondelete="SET NULL"), index=True
    )
    source_item_id: Mapped[int | None] = mapped_column(
        ForeignKey("run_items.id", ondelete="SET NULL"), index=True
    )
    source_label: Mapped[str] = mapped_column(String(80))
    excel_product_name: Mapped[str] = mapped_column(String(255))
    candidate_names: Mapped[list[str]] = mapped_column(JSON, default=list)
    expected_product_code: Mapped[str | None] = mapped_column(String(100))
    expected_metric_values: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    expected_metric_status: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    note: Mapped[str] = mapped_column(Text)
    created_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    is_active: Mapped[bool] = mapped_column(default=True, index=True)

    source_run: Mapped[UpdateRun | None] = relationship(foreign_keys=[source_run_id])
    source_item: Mapped[RunItem | None] = relationship(foreign_keys=[source_item_id])


class OcrRegressionRun(Base):
    __tablename__ = "ocr_regression_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    requested_by: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    status: Mapped[str] = mapped_column(String(30), default="queued", index=True)
    total_count: Mapped[int] = mapped_column(default=0)
    passed_count: Mapped[int] = mapped_column(default=0)
    failed_count: Mapped[int] = mapped_column(default=0)
    skipped_count: Mapped[int] = mapped_column(default=0)
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)

    results: Mapped[list[OcrRegressionResult]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )


class OcrRegressionResult(Base):
    __tablename__ = "ocr_regression_results"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("ocr_regression_runs.id", ondelete="CASCADE"), index=True)
    sample_id: Mapped[int] = mapped_column(ForeignKey("ocr_regression_samples.id"), index=True)
    outcome: Mapped[str] = mapped_column(String(30))
    expected: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    actual: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    detail: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    run: Mapped[OcrRegressionRun] = relationship(back_populates="results")
    sample: Mapped[OcrRegressionSample] = relationship()
```

为 `OcrRegressionRun` 增加 `results` 级联关系；不要为样本增加 `UpdateRun`/`RunItem` 的级联关系。所有 JSON 默认使用 `default=dict` 或 `default=list`，不使用可变对象常量。

- [ ] **Step 4: 写 0006 迁移**

创建 `migrations/versions/0006_add_ocr_regression_loop.py`，`down_revision = "0005_correct_china_times"`。升级顺序为：给 `run_items` 增加 `ocr_evidence` JSON 非空列（已有行填 `{}`），创建三个表、外键和以下索引：

```python
op.create_index("ix_ocr_regression_samples_image_sha256", "ocr_regression_samples", ["image_sha256"])
op.create_index("ix_ocr_regression_runs_status", "ocr_regression_runs", ["status"])
op.create_index("ix_ocr_regression_results_run_id", "ocr_regression_results", ["run_id"])
op.create_index("ix_ocr_regression_results_sample_id", "ocr_regression_results", ["sample_id"])
```

降级顺序为先删除结果表、回归运行表、回归样本表，最后删除 `run_items.ocr_evidence`。在 `tests/test_migrations.py` 断言升级包含列、表、`SET NULL` 外键与降级删除顺序。

- [ ] **Step 5: 运行模型和迁移测试**

Run: `../../.venv/bin/python -m pytest tests/unit/test_ocr_regression.py tests/test_migrations.py -q`

Expected: PASS，且迁移测试无新增失败。

- [ ] **Step 6: 提交数据模型**

```bash
git add app/models.py migrations/versions/0006_add_ocr_regression_loop.py tests/unit/test_ocr_regression.py tests/test_migrations.py
git commit -m "feat: add OCR regression models"
```

## 任务 2：实现样本资产复制、导入和显式提升

**Files:**
- Create: `app/ocr/regression.py`
- Test: `tests/unit/test_ocr_regression.py`
- Test: `tests/e2e/test_lan_flow.py`

- [ ] **Step 1: 写图片去重和校验的失败测试**

在 `tests/unit/test_ocr_regression.py` 用 `tmp_path` 写两个内容相同、文件名不同的 PNG 字节，调用待实现的 `copy_sample_image`：

```python
def test_copy_sample_image_deduplicates_by_sha256(tmp_path: Path) -> None:
    first = tmp_path / "first.png"
    second = tmp_path / "second.png"
    first.write_bytes(b"same-image")
    second.write_bytes(b"same-image")

    first_copy = copy_sample_image(first, tmp_path / "samples")
    second_copy = copy_sample_image(second, tmp_path / "samples")

    assert first_copy.sha256 == second_copy.sha256
    assert first_copy.path == second_copy.path
    assert first_copy.path.read_bytes() == b"same-image"
```

- [ ] **Step 2: 运行测试确认函数不存在**

Run: `../../.venv/bin/python -m pytest tests/unit/test_ocr_regression.py::test_copy_sample_image_deduplicates_by_sha256 -q`

Expected: FAIL，提示 `copy_sample_image` 尚未定义。

- [ ] **Step 3: 实现受保护目录中的图片复制**

在 `app/ocr/regression.py` 定义：

```python
@dataclass(frozen=True)
class SampleImage:
    path: Path
    sha256: str


def copy_sample_image(source: str | Path, samples_root: str | Path) -> SampleImage:
    source_path = Path(source).resolve()
    if not source_path.is_file():
        raise ValueError("样本原图不存在")
    digest = sha256_file(source_path)
    target_root = Path(samples_root).resolve()
    target_root.mkdir(parents=True, exist_ok=True)
    target = target_root / f"{digest}{source_path.suffix.lower()}"
    if target.exists() and sha256_file(target) != digest:
        raise ValueError("样本文件校验值冲突")
    if not target.exists():
        target.write_bytes(source_path.read_bytes())
    return SampleImage(target, digest)
```

只接受图片文件的绝对路径和已配置的 `/data/ocr-quality/samples` 根目录；调用方再检查 `target.is_relative_to(samples_root)`，避免路径穿越。

- [ ] **Step 4: 写样本导入与提升的失败测试**

在 `tests/unit/test_ocr_regression.py` 使用现有 `OcrReviewSample` 和 `RunFile` fixture，覆盖三个情况：

```python
def test_promote_review_sample_copies_image_and_keeps_expected_values(...):
    sample = promote_review_sample(session, sample_id=review_sample.id, samples_root=tmp_path / "samples", actor_id=1)
    assert sample.expected_metric_values["mtd"] == "-0.0633"
    assert Path(sample.image_path).exists()


def test_import_history_skips_multi_image_run_without_source_choice(...):
    result = import_confirmed_samples(session, run_id=run.id, samples_root=tmp_path / "samples", actor_id=1)
    assert result.needs_image_choice == 1
    assert result.created == 0


def test_promote_case_deduplicates_same_image_product_and_expected_values(...):
    first = promote_confirmed_case(...)
    second = promote_confirmed_case(...)
    assert first.id == second.id
```

将 `-0.0633` 作为 Decimal 内部值的字符串保存，避免 JSON 浮点误差。

- [ ] **Step 5: 实现导入/提升服务**

在 `app/ocr/regression.py` 实现以下接口：

```python
def promote_review_sample(
    session: Session, *, sample_id: int, samples_root: Path, actor_id: int,
    source_file_id: int | None = None,
) -> OcrRegressionSample: ...

def promote_confirmed_case(
    session: Session, *, item_id: int, expected_metric_values: Mapping[str, Decimal],
    expected_metric_status: Mapping[str, str], note: str, samples_root: Path,
    actor_id: int, source_file_id: int,
) -> OcrRegressionSample: ...

@dataclass(frozen=True)
class SampleImportResult:
    created: int
    existing: int
    skipped: int
    needs_image_choice: int

def import_confirmed_samples(
    session: Session, *, run_id: int, samples_root: Path, actor_id: int,
) -> SampleImportResult: ...
```

`promote_review_sample` 只接受来源 `image` 或 `none` 的 `OcrReviewSample`，使用其人工确认值和最新产品信息；公募接口来源直接拒绝。没有唯一图片时返回明确的选择错误，不猜测。所有数据库写入由路由外围事务提交，服务函数不调用 `commit()`。写入 `AuditLog`，重复以 `image_sha256 + excel_product_name + expected_metric_values` 去重。

- [ ] **Step 6: 运行样本服务测试**

Run: `../../.venv/bin/python -m pytest tests/unit/test_ocr_regression.py -q`

Expected: PASS，覆盖复制、校验、历史导入、提升和去重。

- [ ] **Step 7: 写删除来源后的集成测试并实现保留**

在 `tests/e2e/test_lan_flow.py` 创建来源批次、提升一个样本、调用现有 `delete_run`，再断言：

```python
assert session.get(OcrRegressionSample, sample.id) is not None
assert Path(sample.image_path).exists()
assert session.get(OcrRegressionSample, sample.id).source_run_id is None
```

若现有删除逻辑因外键约束无法置空，修改 0006 外键为 `ondelete="SET NULL"` 并在 `delete_run` 的提交前不主动删除样本。运行：

`../../.venv/bin/python -m pytest tests/e2e/test_lan_flow.py -k 'regression_sample_survives_run_delete' -q`

- [ ] **Step 8: 提交样本服务**

```bash
git add app/ocr/regression.py tests/unit/test_ocr_regression.py tests/e2e/test_lan_flow.py
git commit -m "feat: preserve OCR regression sample assets"
```

## 任务 3：保存字段证据并实现疑似空值二次识别

**Files:**
- Modify: `app/ocr/table_parser.py`
- Create: `app/ocr/evidence.py`
- Modify: `app/jobs/processor.py`
- Test: `tests/unit/test_ocr_evidence.py`
- Test: `tests/unit/test_table_parser.py`
- Test: `tests/integration/test_jobs.py`

- [ ] **Step 1: 写解析证据的失败测试**

在 `tests/unit/test_ocr_evidence.py` 构造带 box 的 `OCRToken`，断言 `extract_metric_rows` 为 `mtd` 保存原始文本、置信度和四点边界框；现有结果接口仍满足 `row.metrics` 和 `row.blank_metrics` 断言。

```python
def test_metric_row_retains_metric_cell_evidence() -> None:
    rows = extract_metric_rows(tokens_for_product_with_mtd("-6.33"))
    assert rows[0].metric_evidence["mtd"].text == "-6.33"
    assert rows[0].metric_evidence["mtd"].confidence == 0.99
    assert len(rows[0].metric_evidence["mtd"].box) == 4
```

- [ ] **Step 2: 运行失败测试确认没有证据字段**

Run: `../../.venv/bin/python -m pytest tests/unit/test_ocr_evidence.py::test_metric_row_retains_metric_cell_evidence -q`

Expected: FAIL，提示 `OCRMetricRow` 没有 `metric_evidence`。

- [ ] **Step 3: 扩展解析结果保留字段证据**

在 `ParsedCell` 增加 `box: tuple[tuple[float, float], ...]`，在 `group_rows` 从 `OCRToken` 传入；新增：

```python
@dataclass(frozen=True)
class MetricCellEvidence:
    text: str
    confidence: float
    box: tuple[tuple[float, float], ...]


@dataclass(frozen=True)
class OCRMetricRow:
    product_name: str
    product_code: str | None
    metrics: dict[str, Decimal]
    confidence: float
    blank_metrics: frozenset[str] = frozenset()
    metric_evidence: dict[str, MetricCellEvidence] = field(default_factory=dict)
```

给 `product_name`、`product_code` 和每个已解析/空值指标填入证据；保持新增字段位于默认参数之后，使旧测试中的五参数 `OCRMetricRow(...)` 继续有效。`ocr/benchmark.py` 只读取已有字段，不做行为改变。

- [ ] **Step 4: 写二次识别的失败测试**

在 `tests/integration/test_jobs.py` 增加一个 fake OCR service：首轮返回产品名、周收益和孤立 `-` 的 MTD；二次调用返回 `-6.33`。断言 `process_run` 后：

```python
assert item.metric_values["mtd"] == "-0.0633"
assert item.metric_status["mtd"] == "extracted"
assert item.ocr_evidence["metrics"]["mtd"]["passes"] == 2
assert item.ocr_evidence["metrics"]["mtd"]["selected_pass"] == 2
```

再增加两条：两轮均只返回 `-` 时为 `source_blank` 且不进入 `needs_review`；两轮均无可解析值且没有明确破折号时为 `stale`/待审核，不写 `source_blank`。

- [ ] **Step 5: 实现证据序列化与安全裁剪**

在 `app/ocr/evidence.py` 实现：

```python
def metric_row_evidence(row: OCRMetricRow, *, pass_number: int, image_name: str, image_sha256: str) -> dict[str, object]: ...

def merge_metric_passes(first: OCRMetricRow, second: OCRMetricRow | None) -> tuple[OCRMetricRow, dict[str, object]]: ...

def crop_box(image: str | Path, box: tuple[tuple[float, float], ...], destination_root: Path) -> Path: ...
```

`merge_metric_passes` 只用二次结果填补首轮缺失或 `blank_metrics`，不覆盖首轮有效值；二次结果必须能唯一归属于同一产品行。裁剪函数将坐标夹到图片宽高，拒绝空框和不在受管目录下的路径，并按图片 SHA、坐标和 pass 生成稳定文件名。

- [ ] **Step 6: 在处理器增加按条件触发二次识别**

在 `app/jobs/processor.py` 提取一个纯函数：

```python
def needs_dense_second_pass(row: OCRMetricRow | None, expected_metrics: set[str]) -> bool: ...
```

触发条件只包括：行不存在、期待指标缺失、`blank_metrics` 非空或指标无法解析；已有有效值不触发覆盖。对触发图片调用 `recognize_tiled(path, tile_height=800, overlap=192)` 的兼容入口；由于 `OCRRecognizer` 当前协议只有默认参数，新增协议方法 `recognize_tiled_dense`，RapidOCR 实现固定调用 800/192，Paddle 实现复用默认分片并返回相同接口。

把首轮与二次行交给 `merge_metric_passes`，再进入现有 `_find_image_row` 和 `_set_item`。`source_blank` 只来自两轮均存在的明确空值标记。每个截图来源、两轮 token 和最终选择原因写入 `item.ocr_evidence`；公募来源写 `{}`。

- [ ] **Step 7: 运行 OCR 和处理器测试**

Run: `../../.venv/bin/python -m pytest tests/unit/test_ocr_engine.py tests/unit/test_table_parser.py tests/unit/test_ocr_evidence.py tests/integration/test_jobs.py -q`

Expected: PASS，且原有 `source_blank` 测试保持通过。

- [ ] **Step 8: 提交二次识别与证据**

```bash
git add app/ocr/table_parser.py app/ocr/evidence.py app/jobs/processor.py app/ocr/engine.py app/ocr/paddle.py tests/unit/test_ocr_evidence.py tests/unit/test_table_parser.py tests/unit/test_ocr_engine.py tests/integration/test_jobs.py
git commit -m "feat: retry uncertain OCR fields with evidence"
```

## 任务 4：实现后台回归运行器

**Files:**
- Modify: `app/ocr/regression.py`
- Create: `app/jobs/regression_worker.py`
- Modify: `app/jobs/worker.py`
- Modify: `app/config.py`
- Test: `tests/unit/test_ocr_regression.py`
- Test: `tests/integration/test_jobs.py`

- [ ] **Step 1: 写纯比较函数的失败测试**

在 `tests/unit/test_ocr_regression.py` 使用一个期望 MTD `-0.0633`、YTD `0.2567` 的样本和 fake OCR 输出，断言：

```python
assert compare_sample(sample, actual_product_code="P001", actual_values={"mtd": "-0.0633", "ytd": "0.2567"}, actual_status={"mtd": "extracted", "ytd": "extracted"}).outcome == "passed"
assert compare_sample(sample, actual_product_code="P001", actual_values={"mtd": "", "ytd": "0.2567"}, actual_status={"mtd": "source_blank", "ytd": "extracted"}).outcome == "status_mismatch"
```

- [ ] **Step 2: 实现只读样本比较**

实现 `compare_sample`，逐指标比较规范化 Decimal 字符串和状态；缺少实际产品标记 `product_unmatched`，无值标记 `value_missing`，数值不同标记 `value_mismatch`，状态不同标记 `status_mismatch`。不要写任何 SQLAlchemy 对象或文件。

- [ ] **Step 3: 写回归运行器失败测试**

在 `tests/integration/test_jobs.py` 创建两个样本、fake OCR service 和一个 `OcrRegressionRun`，调用 `run_regression(session, run.id, ocr_service=fake, samples_root=tmp_path)`，断言：

```python
assert run.status == "completed"
assert run.total_count == 2
assert run.passed_count == 1
assert run.failed_count == 1
assert len(session.query(OcrRegressionResult).filter_by(run_id=run.id).all()) == 2
assert production_item.metric_values == production_before
```

- [ ] **Step 4: 实现按样本执行并隔离生产数据**

`run_regression` 在开始时将运行状态设为 `running`，只读取启用样本及其图片，使用与生产相同的匹配/解析函数，按样本写 `OcrRegressionResult`，累加摘要后将状态设为 `completed`。单个图片不存在或 SHA 不匹配时写 `sample_file_invalid`，继续下一个；未捕获异常将该样本记为 `execution_failed`。所有更新只作用于 `OcrRegression*` 模型。

- [ ] **Step 5: 增加后台任务轮询**

新增 `app/jobs/regression_worker.py`：

```python
def run_regression_once() -> bool:
    session = SessionLocal()
    try:
        run = claim_next_regression(session)
        if run is None:
            return False
        run_regression(session, run.id, samples_root=ensure_data_dir() / "ocr-quality" / "samples")
        return True
    finally:
        session.close()
```

在 `app/jobs/worker.py` 的 `run_once` 前先调用回归 claim；没有回归任务再 claim 普通 `UpdateRun`。回归任务一次只允许一个 worker 持有，状态超过 30 分钟可被重新领取。新增 `REGRESSION_POLL_SECONDS` 默认 2，不添加新的容器。

- [ ] **Step 6: 运行回归运行器测试**

Run: `../../.venv/bin/python -m pytest tests/unit/test_ocr_regression.py tests/integration/test_jobs.py -k 'regression or second_pass' -q`

Expected: PASS，且生产 `RunItem` 和输出路径未变化。

- [ ] **Step 7: 提交后台回归**

```bash
git add app/ocr/regression.py app/jobs/regression_worker.py app/jobs/worker.py app/config.py tests/unit/test_ocr_regression.py tests/integration/test_jobs.py
git commit -m "feat: run OCR regression checks in worker"
```

## 任务 5：增加质量中心管理员操作与证据查看

**Files:**
- Modify: `app/main.py`
- Modify: `app/quality.py`
- Modify: `app/templates/quality.html`
- Modify: `app/templates/review.html`
- Modify: `app/templates/base.html`
- Modify: `app/static/app.css`
- Test: `tests/e2e/test_lan_flow.py`

- [ ] **Step 1: 写未登录与普通用户权限失败测试**

在 `tests/e2e/test_lan_flow.py` 覆盖：

```python
assert client.get("/quality/regression/run", follow_redirects=False).status_code == 303
assert user_client.post("/quality/samples/import", data={"token": token, "run_id": run.id}).status_code == 403
assert user_client.post("/quality/regression/run", data={"token": token}).status_code == 403
```

管理员可以创建 `OcrRegressionRun` 并得到 `queued` 状态，而不是在 HTTP 请求内运行 OCR。

- [ ] **Step 2: 添加管理员路由和审计日志**

在 `app/main.py` 使用 `require_admin` 依赖增加：

```python
@app.post("/quality/samples/import")
def import_quality_samples(..., admin: User = Depends(require_admin)) -> RedirectResponse: ...

@app.post("/quality/samples/{sample_id}/enable")
def toggle_quality_sample(..., admin: User = Depends(require_admin)) -> RedirectResponse: ...

@app.post("/quality/regression/run")
def queue_quality_regression(..., admin: User = Depends(require_admin)) -> RedirectResponse: ...
```

所有路由先校验 CSRF，再调用服务函数，写 `AuditLog`，提交后回到 `/quality`；`queue_quality_regression` 只创建 queued 记录。重复存在 `queued`/`running` 时返回“已有回归任务运行中”，不创建第二个任务。

- [ ] **Step 3: 增加质量聚合视图模型**

在 `app/quality.py` 增加 `RegressionSummary` 和 `RegressionFailure`，从最近一次运行读取总数/通过/失败/未执行及失败结果。运行不存在时返回全空摘要。不得在模板查询数据库。

- [ ] **Step 4: 添加受保护的证据图片路由**

增加 `GET /updates/{run_id}/items/{item_id}/evidence/{metric}`，检查当前用户登录、条目属于批次、证据中的路径位于 `/data/runs` 或受管样本目录，再以 `FileResponse` 返回；路径不合法或没有证据时返回 404。不要暴露任意 `storage_path` 查询参数。

- [ ] **Step 5: 更新质量页和审核页**

质量页新增回归摘要区：管理员显示“导入历史样本”“运行回归验证”按钮和样本数量；普通用户显示摘要和失败列表。失败列表包含产品、字段、预期/实际值和证据链接。审核页对有 `ocr_evidence` 的字段显示原始文本/置信度/两次识别状态及“查看截图”；历史空证据显示“该历史批次无识别证据”。`source_blank` 不加入待审核入口。

- [ ] **Step 6: 运行页面与权限测试**

Run: `../../.venv/bin/python -m pytest tests/e2e/test_lan_flow.py -k 'quality or evidence or regression' -q`

Expected: PASS，覆盖未登录 303、普通用户 403、管理员排队、页面摘要和图片 404/200。

- [ ] **Step 7: 提交页面和权限**

```bash
git add app/main.py app/quality.py app/templates/quality.html app/templates/review.html app/templates/base.html app/static/app.css tests/e2e/test_lan_flow.py
git commit -m "feat: expose OCR quality loop controls"
```

## 任务 6：部署、提升批次 #12 样本与回归验收

**Files:**
- Modify: `README.md`
- Modify: `docs/superpowers/specs/2026-07-21-ocr-regression-loop-design.md` only if implementation-specific behavior differs
- Test: full repository test suite

- [ ] **Step 1: 增加运维和用户使用说明**

在 README 的 OCR 质量章节补充：质量中心入口、管理员如何导入历史样本、如何提升已复核案例、如何运行回归、`source_blank` 与待审核的区别、样本图片不随更新历史删除，以及回归运行不会改变生产批次。

- [ ] **Step 2: 执行全量本地验证**

Run: `../../.venv/bin/python -m pytest -q && ../../.venv/bin/python -m ruff check . && git diff --check`

Expected: 所有测试通过、Ruff 输出 `All checks passed!`、diff check 无输出。

- [ ] **Step 3: 构建 Docker 镜像**

Run: `docker compose build app worker`

Expected: 两个服务构建退出码为 0；不启动本地生产数据卷。

- [ ] **Step 4: 部署到 Unraid 的隔离构建目录**

在 `root@192.168.5.28:/mnt/user/appdata/nav-updater` 之外创建带提交号的临时构建目录，只上传当前工作树已提交内容和 Docker 配置；执行 `docker compose up -d --build app worker`，不要覆盖服务器未提交源码和 `.env`。

- [ ] **Step 5: 在 Unraid 验证迁移与健康状态**

Run:

```bash
docker compose ps
docker compose exec -T db pg_isready -U nav -d nav
curl -fsS http://127.0.0.1:8080/healthz
```

Expected: app/worker/db 均运行，数据库健康，`/healthz` 返回 `{"status":"ok"}`；日志包含 `0005_correct_china_times -> 0006_add_ocr_regression_loop`。

- [ ] **Step 6: 提升批次 #12 的标准答案**

通过管理员页面或同等已审计服务调用，将批次 #12 条目 #68 的原图、产品名 `浑瑾岳桐金选1号B`、MTD `-0.0633` 和状态 `extracted` 加入样本库；确认样本图片 SHA 与原始 RunFile 相同。不得直接修改 `RunItem` 的生产值。

- [ ] **Step 7: 执行生产回归并核对结果**

在质量中心触发回归，等待 worker 完成。核对：#12 样本为 `passed`；失败项包含明确 expected/actual；批次 #12 的 `RunItem #68` 仍为 `-0.0633`；更新历史时间继续显示北京时间；生产 Excel 的 MTD 仍为 `-6.33`。

- [ ] **Step 8: 请求代码审查并准备合并**

Run: `git status --short --branch && git log --oneline --decorate -8`

确认没有 `.env`、样本图片或临时部署目录进入 Git；本地与 Unraid 验收均有输出证据后，再使用 finishing-a-development-branch 流程决定合并或保留分支。

## 计划自检

- 规格中的样本独立生命周期由任务 1/2/6 覆盖。
- 规格中的二次识别、`source_blank` 优先级和字段证据由任务 3 覆盖。
- 规格中的回归运行摘要、单样本失败隔离和生产只读由任务 4/5 覆盖。
- 规格中的管理员/普通用户权限、受保护图片和审计由任务 5 覆盖。
- 规格中的批次 #12、Docker、Unraid、全量测试验收由任务 6 覆盖。
- 计划中没有自动训练模型、外部 OCR 上传或同步阻塞上传流程。
