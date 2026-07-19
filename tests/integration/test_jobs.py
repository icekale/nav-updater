from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.catalog import import_catalog
from app.db import Base
from app.domain.matching import CatalogRecord
from app.domain.types import NavPoint
from app.excel.template_adapter import TemplateAdapter
from app.jobs.service import (
    RUN_COMPLETED,
    RUN_PROCESSING,
    claim_next_run,
    create_run,
    finish_run,
    metric_values_from_nav,
)
from app.models import Product, UpdateRun, User


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
