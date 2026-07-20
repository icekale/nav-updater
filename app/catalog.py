from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from .domain.matching import CatalogRecord
from .models import Product
from .providers.public_fund import PublicFundRecord


def import_catalog(session: Session, records: Iterable[CatalogRecord]) -> list[Product]:
    records = list(records)
    existing = {product.product_code: product for product in session.scalars(select(Product)).all()}
    imported: list[Product] = []
    for record in records:
        current = existing.get(record.product_code)
        if current and (
            current.product_name != record.product_name
            or current.product_type != record.product_type
        ):
            raise ValueError(f"catalog conflict for product_code {record.product_code}")
        if current:
            imported.append(current)
            continue
        product = Product(
            product_name=record.product_name,
            product_code=record.product_code,
            product_type=record.product_type,
        )
        session.add(product)
        imported.append(product)
    session.flush()
    return imported


def ensure_public_product(
    session: Session, record: PublicFundRecord, source_name: str
) -> tuple[Product, bool]:
    product = session.scalar(select(Product).where(Product.product_code == record.code))
    if product is not None:
        historical_names = list(product.historical_names or [])
        if source_name not in historical_names and source_name != product.product_name:
            product.historical_names = [*historical_names, source_name]
        return product, False
    product = Product(
        product_name=record.name,
        product_code=record.code,
        product_type="public",
        historical_names=[source_name],
    )
    session.add(product)
    session.flush()
    return product, True
