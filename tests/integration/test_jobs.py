from datetime import date

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.catalog import import_catalog
from app.db import Base
from app.domain.matching import CatalogRecord
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
