from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.catalog import import_catalog
from app.db import Base
from app.domain.matching import CatalogRecord
from app.domain.types import NavPoint
from app.excel.template_adapter import TemplateAdapter
from app.jobs.processor import ALL_METRICS, process_run
from app.jobs.service import (
    RUN_COMPLETED,
    RUN_PROCESSING,
    claim_next_run,
    create_run,
    finish_run,
    metric_values_from_nav,
)
from app.models import Product, RunFile, RunItem, UpdateRun, User


def test_catalog_import_persists_products_and_run_state() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    admin = User(username="admin", password_hash="hash", role="admin")
    session.add(admin)
    session.flush()
    imported = import_catalog(
        session,
        [CatalogRecord("易方达环保主题混合A", "001856", "public")],
    )
    run = UpdateRun(operator_id=admin.id, cutoff_date=date(2026, 7, 17), status="uploaded")
    session.add(run)
    session.commit()
    assert imported[0].product_code == "001856"
    assert session.query(Product).count() == 1
    assert session.query(UpdateRun).one().status == "uploaded"


def test_create_run_skips_blank_template_rows_and_claims_work(tmp_path: Path) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    admin = User(username="admin", password_hash="hash", role="admin")
    session.add(admin)
    session.flush()
    workbook = tmp_path / "template.xlsx"
    workbook.write_bytes(Path("tests/fixtures/net_value_template.xlsx").read_bytes())
    run = create_run(
        session,
        operator_id=admin.id,
        cutoff_date=date(2026, 7, 17),
        workbook_path=workbook,
        image_paths=[],
        template=TemplateAdapter(),
    )
    assert len(run.items) == 6
    claimed = claim_next_run(session, now=datetime(2026, 7, 19, 12, 0))
    assert claimed is not None and claimed.id == run.id
    assert claimed.status == RUN_PROCESSING


def test_stale_processing_run_can_be_reclaimed() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    admin = User(username="admin", password_hash="hash", role="admin")
    session.add(admin)
    session.flush()
    run = UpdateRun(
        operator_id=admin.id,
        cutoff_date=date(2026, 7, 17),
        status=RUN_PROCESSING,
        heartbeat_at=datetime(2026, 7, 19, 10, 0),
    )
    session.add(run)
    session.commit()
    reclaimed = claim_next_run(session, now=datetime(2026, 7, 19, 11, 0))
    assert reclaimed is not None and reclaimed.status == RUN_PROCESSING


def test_finish_run_and_metric_adapter() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    admin = User(username="admin", password_hash="hash", role="admin")
    session.add(admin)
    session.flush()
    run = UpdateRun(operator_id=admin.id, cutoff_date=date(2026, 7, 17), status=RUN_PROCESSING)
    session.add(run)
    session.commit()
    finished = finish_run(session, run.id, output_path="/data/out.xlsx")
    assert finished.status == RUN_COMPLETED
    values, statuses = metric_values_from_nav(
        [
            NavPoint(date(2025, 7, 10), Decimal("100")),
            NavPoint(date(2026, 7, 10), Decimal("110")),
            NavPoint(date(2026, 7, 17), Decimal("111")),
        ],
        date(2026, 7, 17),
        "public",
    )
    assert values["weekly"] == Decimal("0.009090909090909090909090909")
    assert statuses["weekly"] == "calculated"


def test_save_manual_review_converts_percentages_and_marks_missing_values_stale() -> None:
    from app.jobs import review

    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    admin = User(username="admin", password_hash="hash", role="admin")
    product = Product(product_name="仁桥金选泽源5B", product_code="P001", product_type="private")
    session.add_all([admin, product])
    session.flush()
    run = UpdateRun(operator_id=admin.id, cutoff_date=date(2026, 7, 17), status="uploaded")
    session.add(run)
    session.flush()
    item = RunItem(
        run_id=run.id,
        excel_row=2,
        original_values={"product_name": "仁桥金选泽源5B"},
    )
    session.add(item)
    session.commit()

    reviewed = review.save_manual_review(
        session,
        item=item,
        product=product,
        inputs={"weekly": "12.34%", "sharpe": "1.25"},
        note="以管理人 7 月 17 日净值表为准",
    )

    assert reviewed.match_source == "manual"
    assert reviewed.row_status == "stale"
    assert reviewed.metric_values == {"weekly": "0.1234", "sharpe": "1.25"}
    assert reviewed.metric_status["weekly"] == "manual"
    assert reviewed.metric_status["mtd"] == "stale"
    assert reviewed.error_reason == "人工审核：以管理人 7 月 17 日净值表为准"


def test_save_manual_review_requires_note_and_at_least_one_metric() -> None:
    from app.jobs import review

    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    admin = User(username="admin", password_hash="hash", role="admin")
    product = Product(product_name="仁桥金选泽源5B", product_code="P001", product_type="private")
    session.add_all([admin, product])
    session.flush()
    run = UpdateRun(operator_id=admin.id, cutoff_date=date(2026, 7, 17), status="uploaded")
    session.add(run)
    session.flush()
    item = RunItem(
        run_id=run.id,
        excel_row=2,
        original_values={"product_name": "仁桥金选泽源5B"},
    )
    session.add(item)
    session.commit()

    with pytest.raises(review.ManualReviewError, match="审核说明"):
        review.save_manual_review(
            session,
            item=item,
            product=product,
            inputs={"weekly": "12.34"},
            note="",
        )

    with pytest.raises(review.ManualReviewError, match="至少填写一个指标"):
        review.save_manual_review(
            session,
            item=item,
            product=product,
            inputs={},
            note="人工核对",
        )


def test_process_run_uses_manual_values_without_calling_provider() -> None:
    class CapturingAdapter:
        updates: dict[int, dict[str, Decimal]]
        stale: dict[int, set[str]]

        def apply_updates(self, input_path, output_path, updates, stale) -> None:
            self.updates = updates
            self.stale = stale

    class FailingProvider:
        def fetch_history(self, product_code: str):
            raise AssertionError(f"provider should not be called for {product_code}")

    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    admin = User(username="admin", password_hash="hash", role="admin")
    product = Product(product_name="仁桥金选泽源5B", product_code="P001", product_type="private")
    session.add_all([admin, product])
    session.flush()
    run = UpdateRun(operator_id=admin.id, cutoff_date=date(2026, 7, 17), status="uploaded")
    session.add(run)
    session.flush()
    session.add(
        RunFile(
            run_id=run.id,
            file_type="workbook",
            original_name="template.xlsx",
            storage_path="/tmp/template.xlsx",
            sha256="0" * 64,
        )
    )
    statuses = {metric: "stale" for metric in ALL_METRICS}
    statuses.update({"weekly": "manual", "sharpe": "manual"})
    item = RunItem(
        run_id=run.id,
        excel_row=2,
        product_id=product.id,
        match_source="manual",
        row_status="stale",
        metric_values={"weekly": "0.1234", "sharpe": "1.25"},
        metric_status=statuses,
        original_values={"product_name": "仁桥金选泽源5B"},
    )
    session.add(item)
    session.commit()
    adapter = CapturingAdapter()

    process_run(
        session,
        run.id,
        provider=FailingProvider(),
        ocr_service=object(),
        adapter=adapter,
    )

    assert adapter.updates[item.excel_row] == {
        "weekly": Decimal("0.1234"),
        "sharpe": Decimal("1.25"),
    }
    assert "mtd" in adapter.stale[item.excel_row]
