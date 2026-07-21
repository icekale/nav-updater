from datetime import date, datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.db import Base
from app.models import Product, RunItem, UpdateRun
from app.monitoring import build_monitoring_dashboard


def _completed_item(
    session: Session,
    *,
    product: Product,
    cutoff_date: date,
    created_at: datetime,
    row_status: str = "ready",
    metric_values: dict[str, str] | None = None,
    metric_status: dict[str, str] | None = None,
) -> RunItem:
    run = UpdateRun(
        cutoff_date=cutoff_date,
        status="completed",
        created_at=created_at,
        finished_at=created_at,
    )
    session.add(run)
    session.flush()
    item = RunItem(
        run_id=run.id,
        product_id=product.id,
        excel_row=2,
        row_status=row_status,
        metric_values=metric_values or {"weekly": "0.01"},
        metric_status=metric_status or {"weekly": "extracted"},
    )
    session.add(item)
    return item


def test_monitoring_uses_latest_completed_private_product_record() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    normal = Product(product_name="正常产品", product_code="P001", product_type="private")
    never_updated = Product(
        product_name="从未更新产品", product_code="P002", product_type="private"
    )
    missing = Product(product_name="缺失产品", product_code="P003", product_type="private")
    outdated = Product(product_name="过期产品", product_code="P004", product_type="private")
    source_blank = Product(product_name="空值确认产品", product_code="P005", product_type="private")
    public = Product(product_name="公募产品", product_code="F001", product_type="public")
    inactive = Product(
        product_name="停用产品", product_code="P006", product_type="private", is_active=False
    )
    session.add_all([normal, never_updated, missing, outdated, source_blank, public, inactive])
    session.flush()
    _completed_item(
        session,
        product=normal,
        cutoff_date=date(2026, 7, 10),
        created_at=datetime(2026, 7, 20),
    )
    _completed_item(
        session,
        product=normal,
        cutoff_date=date(2026, 7, 18),
        created_at=datetime(2026, 7, 18),
    )
    _completed_item(
        session,
        product=missing,
        cutoff_date=date(2026, 7, 19),
        created_at=datetime(2026, 7, 19),
        row_status="partial",
        metric_status={"weekly": "stale"},
    )
    _completed_item(
        session,
        product=outdated,
        cutoff_date=date(2026, 7, 10),
        created_at=datetime(2026, 7, 10),
    )
    _completed_item(
        session,
        product=source_blank,
        cutoff_date=date(2026, 7, 19),
        created_at=datetime(2026, 7, 19),
        metric_values={},
        metric_status={"weekly": "source_blank"},
    )
    _completed_item(
        session,
        product=public,
        cutoff_date=date(2026, 7, 19),
        created_at=datetime(2026, 7, 19),
    )
    _completed_item(
        session,
        product=inactive,
        cutoff_date=date(2026, 7, 19),
        created_at=datetime(2026, 7, 19),
    )
    session.commit()

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
    assert {row.status for row in dashboard.exceptions} == {
        "never_updated",
        "missing_data",
        "outdated",
    }


def test_monitoring_filters_rows_and_exceptions_but_keeps_summary_counts() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    missing = Product(product_name="缺失产品", product_code="P001", product_type="private")
    normal = Product(product_name="正常产品", product_code="P002", product_type="private")
    session.add_all([missing, normal])
    session.flush()
    _completed_item(
        session,
        product=missing,
        cutoff_date=date(2026, 7, 20),
        created_at=datetime(2026, 7, 20),
        row_status="partial",
        metric_status={"weekly": "stale"},
    )
    _completed_item(
        session,
        product=normal,
        cutoff_date=date(2026, 7, 20),
        created_at=datetime(2026, 7, 20),
    )
    session.commit()

    filtered = build_monitoring_dashboard(
        session,
        today=date(2026, 7, 21),
        search="缺失",
        status_filter="missing_data",
    )
    normal_only = build_monitoring_dashboard(
        session,
        today=date(2026, 7, 21),
        status_filter="normal",
    )

    assert [row.product_name for row in filtered.rows] == ["缺失产品"]
    assert [row.product_name for row in filtered.exceptions] == ["缺失产品"]
    assert filtered.total_count == 2
    assert filtered.missing_data_count == 1
    assert {row.status for row in normal_only.rows} == {"normal"}
    assert not normal_only.exceptions
