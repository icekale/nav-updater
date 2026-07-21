from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..domain.matching import normalize_ocr_name
from ..models import (
    AuditLog,
    OcrRegressionResult,
    OcrRegressionRun,
    OcrRegressionSample,
    OcrReviewSample,
    Product,
    RunFile,
    RunItem,
)
from ..time import china_now
from .engine import OCRRecognizer, create_ocr_service
from .evidence import merge_metric_passes
from .table_parser import OCRMetricRow, extract_metric_rows


@dataclass(frozen=True)
class SampleImage:
    path: Path
    sha256: str


@dataclass(frozen=True)
class SampleImportResult:
    created: int
    existing: int
    skipped: int
    needs_image_choice: int


@dataclass(frozen=True)
class SampleComparison:
    outcome: str
    detail: str
    expected: dict[str, object]
    actual: dict[str, object]


class SampleSourceChoiceRequired(ValueError):
    pass


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


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


def _decimal_equal(first: object, second: object) -> bool:
    try:
        return Decimal(str(first)) == Decimal(str(second))
    except Exception:
        return str(first) == str(second)


def compare_sample(
    sample: OcrRegressionSample,
    *,
    actual_product_code: str | None,
    actual_values: Mapping[str, object],
    actual_status: Mapping[str, str],
) -> SampleComparison:
    expected_values = dict(sample.expected_metric_values or {})
    expected_status = dict(sample.expected_metric_status or {})
    actual = {
        "product_code": actual_product_code,
        "metric_values": dict(actual_values),
        "metric_status": dict(actual_status),
    }
    expected = {
        "product_code": sample.expected_product_code,
        "metric_values": expected_values,
        "metric_status": expected_status,
    }
    if actual_product_code != sample.expected_product_code:
        return SampleComparison("product_unmatched", "产品代码不一致", expected, actual)
    status_mismatches = [
        metric
        for metric, expected_value in expected_status.items()
        if actual_status.get(metric) != expected_value
    ]
    if status_mismatches:
        return SampleComparison(
            "status_mismatch",
            "状态不一致：" + ", ".join(sorted(status_mismatches)),
            expected,
            actual,
        )
    missing_values = [
        metric
        for metric in expected_values
        if metric not in actual_values or actual_values[metric] in {None, ""}
    ]
    if missing_values:
        return SampleComparison(
            "value_missing",
            "数值缺失：" + ", ".join(sorted(missing_values)),
            expected,
            actual,
        )
    value_mismatches = [
        metric
        for metric, expected_value in expected_values.items()
        if not _decimal_equal(expected_value, actual_values.get(metric))
    ]
    if value_mismatches:
        return SampleComparison(
            "value_mismatch",
            "数值不一致：" + ", ".join(sorted(value_mismatches)),
            expected,
            actual,
        )
    return SampleComparison("passed", "通过", expected, actual)


def _find_sample_row(sample: OcrRegressionSample, rows: list[OCRMetricRow]) -> OCRMetricRow | None:
    candidates = {normalize_ocr_name(name) for name in sample.candidate_names}
    return next((row for row in rows if normalize_ocr_name(row.product_name) in candidates), None)


def _recognize_sample(
    sample: OcrRegressionSample,
    *,
    ocr_service: OCRRecognizer,
) -> tuple[str | None, dict[str, str], dict[str, str]]:
    first = extract_metric_rows(ocr_service.recognize_tiled(sample.image_path))
    first_row = _find_sample_row(sample, first)
    expected_metrics = set(sample.expected_metric_values) | set(sample.expected_metric_status)
    second_row: OCRMetricRow | None = None
    dense_recognizer = getattr(ocr_service, "recognize_tiled_dense", None)
    if dense_recognizer is not None and (
        first_row is None
        or bool(first_row.blank_metrics)
        or bool(expected_metrics - set(first_row.metrics) - set(first_row.blank_metrics))
    ):
        second = extract_metric_rows(dense_recognizer(sample.image_path))
        second_row = _find_sample_row(sample, second)
    if first_row is None and second_row is None:
        return None, {}, {metric: "stale" for metric in expected_metrics}
    merged, _ = merge_metric_passes(first_row or second_row, second_row if first_row else None)
    values = {key: str(value) for key, value in merged.metrics.items()}
    statuses = {
        key: (
            "extracted"
            if key in merged.metrics
            else "source_blank"
            if key in merged.blank_metrics
            else "stale"
        )
        for key in expected_metrics
    }
    return sample.expected_product_code, values, statuses


def run_regression(
    session: Session,
    run_id: int,
    *,
    samples_root: Path,
    ocr_service: OCRRecognizer | None = None,
) -> OcrRegressionRun:
    run = session.get(OcrRegressionRun, run_id)
    if run is None:
        raise ValueError("回归任务不存在")
    active_samples = session.scalars(
        select(OcrRegressionSample).where(OcrRegressionSample.is_active.is_(True))
    ).all()
    ocr_service = ocr_service or create_ocr_service()
    root = samples_root.resolve()
    run.status = "running"
    run.started_at = china_now()
    run.total_count = len(active_samples)
    run.passed_count = 0
    run.failed_count = 0
    run.skipped_count = 0
    run.error_message = None
    session.commit()
    try:
        for sample in active_samples:
            path = Path(sample.image_path).resolve()
            if not path.is_relative_to(root) or not path.is_file():
                comparison = SampleComparison(
                    "sample_file_invalid",
                    "样本图片不存在或不在受保护目录中",
                    {"product_code": sample.expected_product_code},
                    {},
                )
            elif sha256_file(path) != sample.image_sha256:
                comparison = SampleComparison(
                    "sample_file_invalid",
                    "样本图片校验值不一致",
                    {"product_code": sample.expected_product_code},
                    {},
                )
            else:
                try:
                    product_code, values, statuses = _recognize_sample(
                        sample,
                        ocr_service=ocr_service,
                    )
                    comparison = compare_sample(
                        sample,
                        actual_product_code=product_code,
                        actual_values=values,
                        actual_status=statuses,
                    )
                except Exception as exc:
                    comparison = SampleComparison(
                        "execution_failed",
                        f"回归执行失败：{exc}",
                        {},
                        {},
                    )
            session.add(
                OcrRegressionResult(
                    run_id=run.id,
                    sample_id=sample.id,
                    outcome=comparison.outcome,
                    expected=comparison.expected,
                    actual=comparison.actual,
                    detail=comparison.detail,
                )
            )
            if comparison.outcome == "passed":
                run.passed_count += 1
            else:
                run.failed_count += 1
        run.status = "completed"
        run.finished_at = china_now()
        session.commit()
        return run
    except Exception as exc:
        session.rollback()
        failed_run = session.get(OcrRegressionRun, run_id)
        if failed_run is None:
            raise
        failed_run.status = "failed"
        failed_run.error_message = str(exc)
        failed_run.finished_at = china_now()
        session.commit()
        return failed_run


def claim_next_regression(
    session: Session,
    *,
    now: datetime | None = None,
) -> OcrRegressionRun | None:
    now = now or china_now()
    stale_before = now - timedelta(minutes=30)
    query = (
        select(OcrRegressionRun)
        .where(
            or_(
                OcrRegressionRun.status == "queued",
                (OcrRegressionRun.status == "running")
                & (
                    OcrRegressionRun.started_at.is_(None)
                    | (OcrRegressionRun.started_at < stale_before)
                ),
            )
        )
        .order_by(OcrRegressionRun.id)
        .with_for_update(skip_locked=True)
    )
    run = session.scalars(query).first()
    if run is None:
        return None
    run.status = "running"
    run.started_at = now
    session.commit()
    return run


def _image_file(
    session: Session,
    *,
    run_id: int,
    source_file_id: int | None = None,
) -> RunFile:
    files = session.scalars(
        select(RunFile).where(RunFile.run_id == run_id, RunFile.file_type == "image")
    ).all()
    if source_file_id is not None:
        selected = next((file for file in files if file.id == source_file_id), None)
        if selected is None:
            raise ValueError("所选样本图片不属于来源批次")
        return selected
    if len(files) != 1:
        raise SampleSourceChoiceRequired("来源批次包含多张截图，请选择样本原图")
    return files[0]


def _candidate_names(item: RunItem, product: Product) -> list[str]:
    source_name = str(item.original_values.get("product_name", "")).strip()
    return list(
        dict.fromkeys([source_name, product.product_name, *(product.historical_names or [])])
    )


def _serialized_values(values: Mapping[str, Decimal | str]) -> dict[str, str]:
    return {name: str(value) for name, value in values.items()}


def _existing_sample(
    session: Session,
    *,
    image_sha256: str,
    product_name: str,
    expected_values: dict[str, str],
    expected_status: dict[str, str],
    expected_product_code: str | None,
) -> OcrRegressionSample | None:
    candidates = session.scalars(
        select(OcrRegressionSample).where(
            OcrRegressionSample.image_sha256 == image_sha256,
            OcrRegressionSample.excel_product_name == product_name,
            OcrRegressionSample.expected_product_code == expected_product_code,
        )
    ).all()
    return next(
        (
            sample
            for sample in candidates
            if sample.expected_metric_values == expected_values
            and sample.expected_metric_status == expected_status
        ),
        None,
    )


def _create_or_get_sample(
    session: Session,
    *,
    item: RunItem,
    product: Product,
    image: RunFile,
    expected_values: dict[str, str],
    expected_status: dict[str, str],
    source_label: str,
    note: str,
    samples_root: Path,
    actor_id: int,
) -> tuple[OcrRegressionSample, bool]:
    copied = copy_sample_image(image.storage_path, samples_root)
    product_name = str(item.original_values.get("product_name", ""))
    existing = _existing_sample(
        session,
        image_sha256=copied.sha256,
        product_name=product_name,
        expected_values=expected_values,
        expected_status=expected_status,
        expected_product_code=product.product_code,
    )
    if existing is not None:
        return existing, False
    sample = OcrRegressionSample(
        image_path=str(copied.path),
        image_sha256=copied.sha256,
        source_run_id=item.run_id,
        source_item_id=item.id,
        source_label=source_label,
        excel_product_name=product_name,
        candidate_names=_candidate_names(item, product),
        expected_product_code=product.product_code,
        expected_metric_values=expected_values,
        expected_metric_status=expected_status,
        note=note.strip(),
        created_by=actor_id,
        is_active=True,
    )
    session.add(sample)
    session.flush()
    session.add(
        AuditLog(
            actor_id=actor_id,
            action="create",
            object_type="ocr_regression_sample",
            object_id=str(sample.id),
            context={"source_label": source_label, "product_name": product_name},
        )
    )
    return sample, True


def promote_review_sample(
    session: Session,
    *,
    sample_id: int,
    samples_root: Path,
    actor_id: int,
    source_file_id: int | None = None,
) -> OcrRegressionSample:
    review_sample = session.get(OcrReviewSample, sample_id)
    if review_sample is None:
        raise ValueError("人工审核样本不存在")
    if review_sample.ocr_match_source not in {"image", "none"}:
        raise ValueError("公募数据来源不能作为 OCR 回归样本")
    item = session.get(RunItem, review_sample.run_item_id)
    product = session.get(Product, review_sample.product_id)
    if item is None or product is None:
        raise ValueError("人工审核样本缺少来源产品或条目")
    image = _image_file(session, run_id=review_sample.run_id, source_file_id=source_file_id)
    values = {str(key): str(value) for key, value in review_sample.confirmed_metric_values.items()}
    statuses = {
        str(key): str(value) for key, value in review_sample.confirmed_metric_status.items()
    }
    promoted, _ = _create_or_get_sample(
        session,
        item=item,
        product=product,
        image=image,
        expected_values=values,
        expected_status=statuses,
        source_label="历史人工审核",
        note=review_sample.review_note,
        samples_root=samples_root,
        actor_id=actor_id,
    )
    return promoted


def promote_confirmed_case(
    session: Session,
    *,
    item_id: int,
    expected_metric_values: Mapping[str, Decimal | str],
    expected_metric_status: Mapping[str, str],
    note: str,
    samples_root: Path,
    actor_id: int,
    source_file_id: int,
) -> OcrRegressionSample:
    item = session.get(RunItem, item_id)
    product = session.get(Product, item.product_id if item else None)
    if item is None or product is None:
        raise ValueError("回归案例缺少来源产品或条目")
    image = _image_file(session, run_id=item.run_id, source_file_id=source_file_id)
    promoted, _ = _create_or_get_sample(
        session,
        item=item,
        product=product,
        image=image,
        expected_values=_serialized_values(expected_metric_values),
        expected_status={str(key): str(value) for key, value in expected_metric_status.items()},
        source_label="管理员复核案例",
        note=note,
        samples_root=samples_root,
        actor_id=actor_id,
    )
    return promoted


def import_confirmed_samples(
    session: Session,
    *,
    run_id: int,
    samples_root: Path,
    actor_id: int,
) -> SampleImportResult:
    review_samples = session.scalars(
        select(OcrReviewSample)
        .where(OcrReviewSample.run_id == run_id)
        .order_by(OcrReviewSample.run_item_id, OcrReviewSample.review_version.desc())
    ).all()
    latest_by_item: dict[int, OcrReviewSample] = {}
    for review_sample in review_samples:
        latest_by_item.setdefault(review_sample.run_item_id, review_sample)
    result = SampleImportResult(0, 0, 0, 0)
    for review_sample in latest_by_item.values():
        if not review_sample.confirmed_metric_values:
            result = SampleImportResult(
                result.created, result.existing, result.skipped + 1, result.needs_image_choice
            )
            continue
        try:
            before = session.scalar(
                select(OcrRegressionSample.id).where(
                    OcrRegressionSample.source_item_id == review_sample.run_item_id
                )
            )
            promote_review_sample(
                session,
                sample_id=review_sample.id,
                samples_root=samples_root,
                actor_id=actor_id,
            )
            if before is None:
                result = SampleImportResult(
                    result.created + 1, result.existing, result.skipped, result.needs_image_choice
                )
            else:
                result = SampleImportResult(
                    result.created, result.existing + 1, result.skipped, result.needs_image_choice
                )
        except SampleSourceChoiceRequired:
            result = SampleImportResult(
                result.created, result.existing, result.skipped, result.needs_image_choice + 1
            )
        except (ValueError, OSError):
            result = SampleImportResult(
                result.created, result.existing, result.skipped + 1, result.needs_image_choice
            )
    return result
