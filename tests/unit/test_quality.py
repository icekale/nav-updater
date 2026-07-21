from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import (
    OcrRegressionResult,
    OcrRegressionRun,
    OcrRegressionSample,
    OcrReviewSample,
    Product,
    RunItem,
    UpdateRun,
    User,
)
from app.quality import build_quality_dashboard


def _sample(
    *,
    run_id: int,
    item_id: int,
    product_id: int,
    version: int,
    ocr_values: dict[str, str],
    confirmed_values: dict[str, str],
    created_at: datetime,
) -> OcrReviewSample:
    return OcrReviewSample(
        run_id=run_id,
        run_item_id=item_id,
        product_id=product_id,
        excel_product_name="测试产品",
        review_version=version,
        ocr_match_source="image",
        ocr_product_id=product_id,
        ocr_metric_values=ocr_values,
        ocr_metric_status={"weekly": "extracted"} if ocr_values else {"weekly": "stale"},
        confirmed_metric_values=confirmed_values,
        confirmed_metric_status={"weekly": "manual"},
        review_note="人工确认",
        created_at=created_at,
    )


def test_quality_dashboard_uses_latest_samples_in_the_last_30_days() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    admin = User(username="admin", password_hash="hash", role="admin")
    product = Product(product_name="测试产品", product_code="P001", product_type="private")
    session.add_all([admin, product])
    session.flush()
    run = UpdateRun(
        operator_id=admin.id,
        cutoff_date=date(2026, 7, 17),
        status="completed_with_warnings",
        created_at=datetime(2026, 7, 20),
    )
    session.add(run)
    session.flush()
    matching_item = RunItem(run_id=run.id, excel_row=2, row_status="ready")
    missing_item = RunItem(run_id=run.id, excel_row=3, row_status="needs_review")
    incorrect_item = RunItem(
        run_id=run.id,
        excel_row=4,
        row_status="stale",
        metric_status={"mtd": "source_blank"},
    )
    old_item = RunItem(run_id=run.id, excel_row=5, row_status="ready")
    session.add_all([matching_item, missing_item, incorrect_item, old_item])
    session.flush()
    session.add_all(
        [
            _sample(
                run_id=run.id,
                item_id=matching_item.id,
                product_id=product.id,
                version=1,
                ocr_values={"weekly": "0.99"},
                confirmed_values={"weekly": "0.01"},
                created_at=datetime(2026, 7, 19),
            ),
            _sample(
                run_id=run.id,
                item_id=matching_item.id,
                product_id=product.id,
                version=2,
                ocr_values={"weekly": "0.010"},
                confirmed_values={"weekly": "0.01"},
                created_at=datetime(2026, 7, 20),
            ),
            _sample(
                run_id=run.id,
                item_id=missing_item.id,
                product_id=product.id,
                version=1,
                ocr_values={},
                confirmed_values={"weekly": "0.02"},
                created_at=datetime(2026, 7, 20),
            ),
            _sample(
                run_id=run.id,
                item_id=incorrect_item.id,
                product_id=product.id,
                version=1,
                ocr_values={"weekly": "0.03"},
                confirmed_values={"weekly": "0.04"},
                created_at=datetime(2026, 7, 20),
            ),
            _sample(
                run_id=run.id,
                item_id=old_item.id,
                product_id=product.id,
                version=1,
                ocr_values={"weekly": "0.05"},
                confirmed_values={"weekly": "0.05"},
                created_at=datetime(2026, 6, 20),
            ),
        ]
    )
    session.commit()

    dashboard = build_quality_dashboard(session, now=datetime(2026, 7, 21))

    weekly = dashboard.fields[0]
    assert weekly.confirmed_count == 3
    assert weekly.matched_count == 1
    assert weekly.missing_count == 1
    assert weekly.incorrect_count == 1
    assert weekly.accuracy == Decimal("0.3333")
    assert dashboard.pending_review_count == 2
    assert dashboard.source_blank_count == 1


def test_quality_dashboard_includes_latest_regression_summary() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    admin = User(username="admin", password_hash="hash", role="admin")
    session.add(admin)
    session.flush()
    sample = OcrRegressionSample(
        image_path="/data/ocr-quality/samples/a.png",
        image_sha256="a" * 64,
        source_label="管理员复核案例",
        excel_product_name="产品A",
        candidate_names=["产品A"],
        expected_product_code="P001",
        expected_metric_values={"mtd": "-0.0633"},
        expected_metric_status={"mtd": "extracted"},
        note="确认",
        is_active=True,
    )
    session.add(sample)
    session.flush()
    run = OcrRegressionRun(
        requested_by=admin.id,
        status="completed",
        total_count=1,
        passed_count=0,
        failed_count=1,
        finished_at=datetime(2026, 7, 21, 10, 0),
    )
    session.add(run)
    session.flush()
    session.add(
        OcrRegressionResult(
            run_id=run.id,
            sample_id=sample.id,
            outcome="value_mismatch",
            expected={"mtd": "-0.0633"},
            actual={"mtd": ""},
            detail="数值不一致：mtd",
        )
    )
    session.commit()

    dashboard = build_quality_dashboard(session, now=datetime(2026, 7, 21, 11, 0))

    assert dashboard.regression.total_count == 1
    assert dashboard.regression.failed_count == 1
    assert dashboard.regression.failures[0].outcome == "value_mismatch"
