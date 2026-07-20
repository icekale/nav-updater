from __future__ import annotations

import hashlib
from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from .domain.matching import CatalogRecord, normalize_name
from .models import Product
from .providers.public_fund import PublicFundRecord


class PrivateProductError(ValueError):
    pass


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


def matching_active_products(session: Session, product_name: str) -> list[Product]:
    normalized = normalize_name(product_name)
    if not normalized:
        return []
    return [
        product
        for product in session.scalars(select(Product).where(Product.is_active.is_(True))).all()
        if normalize_name(product.product_name) == normalized
        or any(normalize_name(alias) == normalized for alias in product.historical_names or [])
    ]


def private_product_code(product_name: str) -> str:
    normalized = normalize_name(product_name)
    if not normalized:
        raise PrivateProductError("Excel 产品名称不能为空")
    digest = hashlib.sha256(normalized.encode()).hexdigest()[:12]
    return f"private-{digest}"


def get_or_create_private_product(session: Session, product_name: str) -> tuple[Product, bool]:
    matches = matching_active_products(session, product_name)
    if len(matches) == 1:
        return matches[0], False
    if len(matches) > 1:
        raise PrivateProductError("多个激活产品与 Excel 产品名称匹配，请明确选择产品")
    code = private_product_code(product_name)
    if session.scalar(select(Product).where(Product.product_code == code)) is not None:
        raise PrivateProductError("内部产品编号冲突")
    product = Product(product_name=product_name.strip(), product_code=code, product_type="private")
    session.add(product)
    session.flush()
    return product, True


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
