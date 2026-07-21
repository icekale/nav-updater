from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import (
    AuditLog,
    OcrRegressionResult,
    OcrRegressionRun,
    OcrRegressionSample,
    OcrReviewSample,
    Product,
    RunFile,
    RunItem,
    UpdateRun,
    User,
)
from app.ocr.regression import (
    _find_sample_row,
    _recognize_sample,
    claim_next_regression,
    compare_sample,
    copy_sample_image,
    import_confirmed_samples,
    promote_confirmed_case,
    promote_review_sample,
)
from app.ocr.table_parser import OCRMetricRow


def test_regression_models_keep_sample_when_source_run_is_deleted() -> None:
    assert OcrRegressionSample.__tablename__ == "ocr_regression_samples"
    assert OcrRegressionRun.__tablename__ == "ocr_regression_runs"
    assert OcrRegressionResult.__tablename__ == "ocr_regression_results"
    assert "ocr_evidence" in RunItem.__table__.c
    assert OcrRegressionSample.source_run_id.property.columns[0].nullable is True
    assert OcrRegressionSample.created_at.default is not None


def test_only_one_active_regression_run_can_exist() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    admin = User(username="admin", password_hash="hash", role="admin")
    session.add(admin)
    session.flush()
    session.add(OcrRegressionRun(requested_by=admin.id, status="queued"))
    session.commit()

    session.add(OcrRegressionRun(requested_by=admin.id, status="running"))
    with pytest.raises(IntegrityError):
        session.commit()


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


def _review_sample_session(tmp_path: Path, *, image_count: int = 1):
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    admin = User(username="admin", password_hash="hash", role="admin")
    product = Product(product_name="浑瑾岳桐金选1号B", product_code="P001", product_type="private")
    session.add_all([admin, product])
    session.flush()
    run = UpdateRun(operator_id=admin.id, cutoff_date=date(2026, 7, 17), status="completed")
    session.add(run)
    session.flush()
    image_files = []
    for index in range(image_count):
        image_path = tmp_path / f"report-{index}.png"
        image_path.write_bytes(f"image-{index}".encode())
        image_files.append(
            RunFile(
                run_id=run.id,
                file_type="image",
                original_name=image_path.name,
                storage_path=str(image_path),
                sha256="0" * 64,
            )
        )
    session.add_all(image_files)
    session.flush()
    item = RunItem(
        run_id=run.id,
        excel_row=3,
        product_id=product.id,
        match_source="image",
        row_status="stale",
        original_values={"product_name": product.product_name},
        metric_values={"mtd": ""},
        metric_status={"mtd": "source_blank"},
    )
    session.add(item)
    session.flush()
    review_sample = OcrReviewSample(
        run_id=run.id,
        run_item_id=item.id,
        actor_id=admin.id,
        product_id=product.id,
        excel_product_name=product.product_name,
        review_version=1,
        ocr_match_source="image",
        ocr_product_id=product.id,
        ocr_metric_values={"mtd": ""},
        ocr_metric_status={"mtd": "source_blank"},
        confirmed_metric_values={"mtd": "-0.0633"},
        confirmed_metric_status={"mtd": "manual"},
        review_note="批次 #12 复核",
    )
    session.add(review_sample)
    session.commit()
    return session, admin, product, run, item, review_sample, image_files


def test_promote_review_sample_copies_image_and_keeps_expected_values(tmp_path: Path) -> None:
    session, admin, product, run, item, review_sample, image_files = _review_sample_session(
        tmp_path
    )
    promoted = promote_review_sample(
        session,
        sample_id=review_sample.id,
        samples_root=tmp_path / "samples",
        actor_id=admin.id,
    )

    assert promoted.expected_metric_values["mtd"] == "-0.0633"
    assert Path(promoted.image_path).exists()
    assert promoted.source_run_id == run.id
    assert promoted.source_item_id == item.id
    assert promoted.expected_product_code == product.product_code
    assert promoted.expected_metric_status == {"mtd": "extracted"}
    assert Path(promoted.image_path).read_bytes() == Path(image_files[0].storage_path).read_bytes()
    audit = session.query(AuditLog).filter_by(object_type="ocr_regression_sample").one()
    assert audit.object_id == str(promoted.id)


def test_import_history_skips_multi_image_run_without_source_choice(tmp_path: Path) -> None:
    session, admin, _, run, _, _, _ = _review_sample_session(tmp_path, image_count=2)
    result = import_confirmed_samples(
        session,
        run_id=run.id,
        samples_root=tmp_path / "samples",
        actor_id=admin.id,
    )

    assert result.needs_image_choice == 1
    assert result.created == 0


def test_promote_case_deduplicates_same_image_product_and_expected_values(tmp_path: Path) -> None:
    session, admin, product, run, item, _, image_files = _review_sample_session(tmp_path)
    values = {"mtd": Decimal("-0.0633")}
    statuses = {"mtd": "extracted"}
    first = promote_confirmed_case(
        session,
        item_id=item.id,
        expected_metric_values=values,
        expected_metric_status=statuses,
        note="批次 #12 重跑确认",
        samples_root=tmp_path / "samples",
        actor_id=admin.id,
        source_file_id=image_files[0].id,
    )
    second = promote_confirmed_case(
        session,
        item_id=item.id,
        expected_metric_values=values,
        expected_metric_status=statuses,
        note="批次 #12 重跑确认",
        samples_root=tmp_path / "samples",
        actor_id=admin.id,
        source_file_id=image_files[0].id,
    )

    assert first.id == second.id
    assert second.expected_product_code == product.product_code
    assert second.source_run_id == run.id


def test_compare_sample_reports_value_and_status_mismatches() -> None:
    sample = OcrRegressionSample(
        expected_product_code="P001",
        expected_metric_values={"mtd": "-0.0633", "ytd": "0.2567"},
        expected_metric_status={"mtd": "extracted", "ytd": "extracted"},
    )

    passed = compare_sample(
        sample,
        actual_product_code="P001",
        actual_values={"mtd": "-0.0633", "ytd": "0.2567"},
        actual_status={"mtd": "extracted", "ytd": "extracted"},
    )
    mismatch = compare_sample(
        sample,
        actual_product_code="P001",
        actual_values={"mtd": "", "ytd": "0.2567"},
        actual_status={"mtd": "source_blank", "ytd": "extracted"},
    )

    assert passed.outcome == "passed"
    assert mismatch.outcome == "status_mismatch"
    assert "mtd" in mismatch.detail


def test_regression_uses_ocr_product_code_when_comparing_sample(monkeypatch) -> None:
    sample = OcrRegressionSample(
        candidate_names=["产品A"],
        expected_product_code="P001",
        expected_metric_values={"mtd": "0.01"},
        expected_metric_status={"mtd": "extracted"},
    )

    class FakeOCR:
        def recognize_tiled(self, path: str) -> list:
            return []

    monkeypatch.setattr(
        "app.ocr.regression.extract_metric_rows",
        lambda tokens: [
            OCRMetricRow(
                product_name="产品A",
                product_code="P002",
                metrics={"mtd": Decimal("0.01")},
                confidence=0.99,
            )
        ],
    )

    actual_code, values, statuses = _recognize_sample(sample, ocr_service=FakeOCR())

    assert actual_code == "P002"
    assert values == {"mtd": "0.01"}
    assert statuses == {"mtd": "extracted"}


def test_regression_rejects_duplicate_sample_rows() -> None:
    sample = OcrRegressionSample(
        candidate_names=["产品A"],
        expected_product_code="P001",
    )
    rows = [
        OCRMetricRow("产品A", None, {}, 0.9),
        OCRMetricRow("产品A", None, {}, 0.9),
    ]

    assert _find_sample_row(sample, rows) is None


def test_claim_next_regression_moves_queued_run_to_running() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    admin = User(username="admin", password_hash="hash", role="admin")
    session.add(admin)
    session.flush()
    run = OcrRegressionRun(requested_by=admin.id, status="queued")
    session.add(run)
    session.commit()

    claimed = claim_next_regression(session, now=datetime(2026, 7, 21, 10, 0))

    assert claimed is not None
    assert claimed.status == "running"
    assert claimed.started_at == datetime(2026, 7, 21, 10, 0)


def test_regression_worker_runs_claimed_task(monkeypatch, tmp_path: Path) -> None:
    from app.jobs import regression_worker

    calls: list[tuple[int, Path]] = []

    class FakeSession:
        def close(self) -> None:
            calls.append((-1, tmp_path))

    class FakeRun:
        id = 7

    monkeypatch.setattr(regression_worker, "SessionLocal", lambda: FakeSession())
    monkeypatch.setattr(regression_worker, "claim_next_regression", lambda session: FakeRun())
    monkeypatch.setattr(
        regression_worker,
        "run_regression",
        lambda session, run_id, samples_root: calls.append((run_id, samples_root)),
    )
    monkeypatch.setattr(regression_worker, "ensure_data_dir", lambda: tmp_path)

    assert regression_worker.run_once() is True
    assert (7, tmp_path / "ocr-quality" / "samples") in calls


def test_regression_worker_marks_claimed_task_failed_when_execution_raises(
    monkeypatch, tmp_path: Path
) -> None:
    from app.jobs import regression_worker

    class FakeRun:
        id = 7
        status = "running"
        error_message = None
        finished_at = None

    class FakeSession:
        def commit(self) -> None:
            return None

        def close(self) -> None:
            return None

    run = FakeRun()
    monkeypatch.setattr(regression_worker, "SessionLocal", lambda: FakeSession())
    monkeypatch.setattr(regression_worker, "claim_next_regression", lambda session: run)
    monkeypatch.setattr(
        regression_worker,
        "run_regression",
        lambda session, run_id, samples_root: (
            _ for _ in ()
        ).throw(RuntimeError("ocr unavailable")),
    )
    monkeypatch.setattr(regression_worker, "ensure_data_dir", lambda: tmp_path)

    assert regression_worker.run_once() is True
    assert run.status == "failed"
    assert run.error_message == "ocr unavailable"
    assert run.finished_at is not None
