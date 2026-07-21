import hashlib
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import catalog
from app.catalog import import_catalog
from app.db import Base
from app.domain.matching import CatalogRecord
from app.domain.types import NavPoint
from app.excel.template_adapter import TemplateAdapter
from app.jobs.processor import ALL_METRICS, _find_image_row, _image_row_status, process_run
from app.jobs.service import (
    RUN_COMPLETED,
    RUN_PROCESSING,
    claim_next_run,
    create_run,
    finish_run,
    lock_run_item,
    metric_values_from_nav,
)
from app.models import (
    AuditLog,
    NavObservation,
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
from app.ocr.engine import OCRToken
from app.ocr.regression import run_regression
from app.ocr.table_parser import OCRMetricRow
from app.providers.public_fund import PublicFundRecord


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


def test_get_or_create_private_product_uses_a_stable_code() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()

    product, created = catalog.get_or_create_private_product(session, "仁桥 金选泽源5B")

    assert created is True
    assert product.product_name == "仁桥 金选泽源5B"
    assert product.product_type == "private"
    assert product.product_code.startswith("private-")
    assert len(product.product_code) == len("private-") + 12

    reused, reused_created = catalog.get_or_create_private_product(session, "仁桥金选泽源5B")

    assert (reused.id, reused_created) == (product.id, False)


def test_get_or_create_private_product_rejects_ambiguous_name() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add_all(
        [
            Product(product_name="产品A", product_code="P001", product_type="private"),
            Product(
                product_name="产品B",
                product_code="P002",
                product_type="private",
                historical_names=["产品A"],
            ),
        ]
    )
    session.commit()

    assert len(catalog.matching_active_products(session, "产品A")) == 2
    with pytest.raises(catalog.PrivateProductError, match="多个激活产品"):
        catalog.get_or_create_private_product(session, "产品A")


def test_get_or_create_private_product_rejects_internal_code_collision() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    product_name = "测试私募冲突"
    session.add(
        Product(
            product_name="其他产品",
            product_code=catalog.private_product_code(product_name),
            product_type="private",
        )
    )
    session.commit()

    with pytest.raises(catalog.PrivateProductError, match="内部产品编号冲突"):
        catalog.get_or_create_private_product(session, product_name)


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


def test_capture_ocr_review_sample_versions_and_skips_public_provider() -> None:
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
        product_id=product.id,
        match_source="image",
        original_values={"product_name": "仁桥金选泽源5B"},
        metric_values={"weekly": "0.01"},
        metric_status={"weekly": "extracted"},
    )
    public_item = RunItem(
        run_id=run.id,
        excel_row=3,
        product_id=product.id,
        match_source="public_provider",
        original_values={"product_name": "公开基金"},
    )
    session.add_all([item, public_item])
    session.flush()

    first = review.capture_ocr_review_sample(
        session,
        run_id=run.id,
        item=item,
        actor_id=admin.id,
        product=product,
        values={"weekly": Decimal("0.01")},
        note="人工确认",
    )
    assert first is not None
    item.match_source = "manual"
    item.metric_values = {"weekly": "0.01"}
    item.metric_status = {"weekly": "manual"}
    second = review.capture_ocr_review_sample(
        session,
        run_id=run.id,
        item=item,
        actor_id=admin.id,
        product=product,
        values={"weekly": Decimal("0.012")},
        note="再次确认",
    )

    assert second is not None
    assert review.capture_ocr_review_sample(
        session,
        run_id=run.id,
        item=public_item,
        actor_id=admin.id,
        product=product,
        values={"weekly": Decimal("0.01")},
        note="公募不计入 OCR",
    ) is None
    session.flush()
    samples = session.query(OcrReviewSample).order_by(OcrReviewSample.review_version).all()
    assert [sample.review_version for sample in samples] == [1, 2]
    assert samples[0].ocr_metric_values == {"weekly": "0.01"}
    assert samples[1].ocr_metric_values == {"weekly": "0.01"}
    assert samples[1].confirmed_metric_values == {"weekly": "0.012"}


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


def test_process_run_matches_screenshot_without_catalog_product() -> None:
    class CapturingAdapter:
        updates: dict[int, dict[str, Decimal]]

        def apply_updates(self, input_path, output_path, updates, stale) -> None:
            self.updates = updates

    def token(text: str, left: float, top: float) -> OCRToken:
        return OCRToken(
            text,
            ((left, top), (left + 50, top), (left + 50, top + 20), (left, top + 20)),
            0.99,
        )

    class FakeTiledOCR:
        def recognize_tiled(self, path: str) -> list[OCRToken]:
            return [
                token("产品名称", 10, 10),
                token("近一周(%)", 100, 10),
                token("仁桥金选泽源5B", 10, 50),
                token("5.20%", 100, 50),
            ]

    class FailingProvider:
        def resolve_by_name(self, product_name: str):
            raise AssertionError(f"provider should not be called for {product_name}")

    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    admin = User(username="admin", password_hash="hash", role="admin")
    session.add(admin)
    session.flush()
    run = UpdateRun(operator_id=admin.id, cutoff_date=date(2026, 7, 17), status="uploaded")
    session.add(run)
    session.flush()
    session.add_all(
        [
            RunFile(
                run_id=run.id,
                file_type="workbook",
                original_name="template.xlsx",
                storage_path="/tmp/template.xlsx",
                sha256="0" * 64,
            ),
            RunFile(
                run_id=run.id,
                file_type="image",
                original_name="long.png",
                storage_path="/tmp/long.png",
                sha256="1" * 64,
            ),
        ]
    )
    item = RunItem(
        run_id=run.id,
        excel_row=2,
        original_values={"product_name": "仁桥金选泽源5B"},
    )
    session.add(item)
    session.commit()
    adapter = CapturingAdapter()

    process_run(
        session,
        run.id,
        ocr_service=FakeTiledOCR(),
        provider=FailingProvider(),
        adapter=adapter,
    )

    assert item.match_source == "image"
    assert item.product_id is None
    assert adapter.updates[item.excel_row] == {"weekly": Decimal("0.052")}


def test_process_run_routes_high_coverage_ocr_metrics_to_partial() -> None:
    class CapturingAdapter:
        updates: dict[int, dict[str, Decimal]]
        stale: dict[int, set[str]]

        def apply_updates(self, input_path, output_path, updates, stale) -> None:
            self.updates = updates
            self.stale = stale

    def token(text: str, left: float, top: float) -> OCRToken:
        return OCRToken(
            text,
            ((left, top), (left + 50, top), (left + 50, top + 20), (left, top + 20)),
            0.99,
        )

    class FakeTiledOCR:
        def recognize_tiled(self, path: str) -> list[OCRToken]:
            headers = [
                "近一周(%)",
                "MTD(%)",
                "YTD(%)",
                "2019(%)",
                "2020(%)",
                "2021(%)",
                "2022(%)",
                "2023(%)",
                "2024(%)",
                "2025(%)",
                "近一年夏普比",
                "近一年最大回撤(%)",
            ]
            return [
                token("产品名称", 10, 10),
                *(token(header, (index + 1) * 100, 10) for index, header in enumerate(headers)),
                token("仁桥金选泽源5B", 10, 50),
                *(token("5.20%", (index + 1) * 100, 50) for index in range(9)),
            ]

    class FailingProvider:
        def resolve_by_name(self, product_name: str):
            raise AssertionError(f"provider should not be called for {product_name}")

    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    admin = User(username="admin", password_hash="hash", role="admin")
    session.add(admin)
    session.flush()
    run = UpdateRun(operator_id=admin.id, cutoff_date=date(2026, 7, 17), status="uploaded")
    session.add(run)
    session.flush()
    session.add_all(
        [
            RunFile(
                run_id=run.id,
                file_type="workbook",
                original_name="template.xlsx",
                storage_path="/tmp/template.xlsx",
                sha256="0" * 64,
            ),
            RunFile(
                run_id=run.id,
                file_type="image",
                original_name="report.png",
                storage_path="/tmp/report.png",
                sha256="1" * 64,
            ),
        ]
    )
    item = RunItem(
        run_id=run.id,
        excel_row=2,
        original_values={"product_name": "仁桥金选泽源5B"},
    )
    session.add(item)
    session.commit()
    adapter = CapturingAdapter()

    process_run(
        session,
        run.id,
        ocr_service=FakeTiledOCR(),
        provider=FailingProvider(),
        adapter=adapter,
    )

    missing_metrics = {"annual_2025", "sharpe", "max_drawdown"}
    assert item.row_status == "partial"
    assert item.metric_status["weekly"] == "extracted"
    assert {key for key, value in item.metric_status.items() if value == "stale"} == missing_metrics
    assert adapter.stale[item.excel_row] == missing_metrics
    assert adapter.updates[item.excel_row]["weekly"] == Decimal("0.052")


def test_image_row_status_routes_low_coverage_rows_to_manual_review() -> None:
    row = OCRMetricRow(
        product_name="产品A",
        product_code=None,
        metrics={"weekly": Decimal("0.01")},
        confidence=0.99,
    )
    missing_metrics = set(ALL_METRICS) - set(row.metrics)

    assert _image_row_status(row, missing_metrics) == (
        "needs_review",
        (
            "本次未识别：MTD（%）, YTD（%）, 2019（%）, 2020（%）, 2021（%）, 2022（%）, "
            "2023（%）, 2024（%）, 2025（%）, 近一年夏普比, 近一年最大回撤（%）"
        ),
    )


def test_image_row_status_keeps_high_coverage_low_confidence_rows_nonblocking() -> None:
    metrics = {metric: Decimal("0.01") for metric in ALL_METRICS[:9]}
    row = OCRMetricRow(
        product_name="产品A",
        product_code=None,
        metrics=metrics,
        confidence=0.40,
    )
    missing_metrics = {"annual_2025", "sharpe", "max_drawdown"}

    assert _image_row_status(row, missing_metrics) == (
        "partial",
        "本次未识别：2025（%）, 近一年夏普比, 近一年最大回撤（%）；OCR 置信度较低",
    )


def test_process_run_clears_confirmed_source_blank_metrics_without_review() -> None:
    class CapturingAdapter:
        updates: dict[int, dict[str, Decimal | None]]
        stale: dict[int, set[str]]

        def apply_updates(self, input_path, output_path, updates, stale) -> None:
            self.updates = updates
            self.stale = stale

    def token(text: str, left: float, top: float) -> OCRToken:
        return OCRToken(
            text,
            ((left, top), (left + 50, top), (left + 50, top + 20), (left, top + 20)),
            0.99,
        )

    class FakeTiledOCR:
        def recognize_tiled(self, path: str) -> list[OCRToken]:
            headers = [
                "近一周(%)",
                "MTD(%)",
                "YTD(%)",
                "2019(%)",
                "2020(%)",
                "2021(%)",
                "2022(%)",
                "2023(%)",
                "2024(%)",
                "2025(%)",
                "近一年夏普比",
                "近一年最大回撤(%)",
            ]
            values = ["1.00%", "--", "1.00%", "--", "1.00%"] + ["1.00%"] * 5 + [
                "1.25",
                "1.00%",
            ]
            return [
                token("产品名称", 10, 10),
                *(token(header, 100 + index * 100, 10) for index, header in enumerate(headers)),
                token("产品A", 10, 50),
                *(token(value, 100 + index * 100, 50) for index, value in enumerate(values)),
            ]

    class FailingProvider:
        def resolve_by_name(self, product_name: str):
            raise AssertionError(f"provider should not be called for {product_name}")

    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    admin = User(username="admin", password_hash="hash", role="admin")
    session.add(admin)
    session.flush()
    run = UpdateRun(operator_id=admin.id, cutoff_date=date(2026, 7, 17), status="uploaded")
    session.add(run)
    session.flush()
    session.add_all(
        [
            RunFile(
                run_id=run.id,
                file_type="workbook",
                original_name="template.xlsx",
                storage_path="/tmp/template.xlsx",
                sha256="0" * 64,
            ),
            RunFile(
                run_id=run.id,
                file_type="image",
                original_name="report.png",
                storage_path="/tmp/report.png",
                sha256="1" * 64,
            ),
        ]
    )
    item = RunItem(
        run_id=run.id,
        excel_row=2,
        original_values={"product_name": "产品A"},
    )
    session.add(item)
    session.commit()
    adapter = CapturingAdapter()

    process_run(
        session,
        run.id,
        ocr_service=FakeTiledOCR(),
        provider=FailingProvider(),
        adapter=adapter,
    )

    assert item.row_status == "ready"
    assert item.metric_status["weekly"] == "extracted"
    assert item.metric_status["mtd"] == "source_blank"
    assert item.metric_status["annual_2019"] == "source_blank"
    assert adapter.updates[item.excel_row]["mtd"] is None
    assert adapter.updates[item.excel_row]["annual_2019"] is None
    assert adapter.stale[item.excel_row] == set()


def test_process_run_retries_isolated_blank_and_preserves_ocr_evidence() -> None:
    class CapturingAdapter:
        updates: dict[int, dict[str, Decimal | None]]

        def apply_updates(self, input_path, output_path, updates, stale) -> None:
            self.updates = updates

    def token(text: str, left: float, top: float) -> OCRToken:
        return OCRToken(
            text,
            ((left, top), (left + 50, top), (left + 50, top + 20), (left, top + 20)),
            0.99,
        )

    class FakeTiledOCR:
        def __init__(self) -> None:
            self.dense_calls = 0

        def recognize_tiled(self, path: str) -> list[OCRToken]:
            return [
                token("产品名称", 10, 10),
                token("近一周(%)", 100, 10),
                token("MTD(%)", 200, 10),
                token("产品A", 10, 50),
                token("1.00%", 100, 50),
                token("-", 200, 50),
            ]

        def recognize_tiled_dense(self, path: str) -> list[OCRToken]:
            self.dense_calls += 1
            return [
                token("产品名称", 10, 10),
                token("近一周(%)", 100, 10),
                token("MTD(%)", 200, 10),
                token("产品A", 10, 50),
                token("1.00%", 100, 50),
                token("-6.33", 200, 50),
            ]

    class FailingProvider:
        def resolve_by_name(self, product_name: str):
            raise AssertionError(f"provider should not be called for {product_name}")

    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    admin = User(username="admin", password_hash="hash", role="admin")
    session.add(admin)
    session.flush()
    run = UpdateRun(operator_id=admin.id, cutoff_date=date(2026, 7, 17), status="uploaded")
    session.add(run)
    session.flush()
    session.add_all(
        [
            RunFile(
                run_id=run.id,
                file_type="workbook",
                original_name="template.xlsx",
                storage_path="/tmp/template.xlsx",
                sha256="0" * 64,
            ),
            RunFile(
                run_id=run.id,
                file_type="image",
                original_name="report.png",
                storage_path="/tmp/report.png",
                sha256="1" * 64,
            ),
            RunItem(
                run_id=run.id,
                excel_row=2,
                original_values={"product_name": "产品A"},
            ),
        ]
    )
    session.commit()
    adapter = CapturingAdapter()
    ocr = FakeTiledOCR()

    process_run(
        session,
        run.id,
        ocr_service=ocr,
        provider=FailingProvider(),
        adapter=adapter,
    )

    item = session.query(RunItem).filter_by(run_id=run.id).one()
    assert ocr.dense_calls == 1
    assert item.metric_values["mtd"] == "-0.0633"
    assert item.metric_status["mtd"] == "extracted"
    assert item.ocr_evidence["metrics"]["mtd"]["selected_pass"] == 2
    assert adapter.updates[item.excel_row]["mtd"] == Decimal("-0.0633")


def test_run_regression_isolates_production_items_and_records_each_result(tmp_path: Path) -> None:
    def token(text: str, left: float, top: float) -> OCRToken:
        return OCRToken(
            text,
            ((left, top), (left + 50, top), (left + 50, top + 20), (left, top + 20)),
            0.99,
        )

    class FakeOCR:
        def recognize_tiled(self, path: str) -> list[OCRToken]:
            return [
                token("产品名称", 10, 10),
                token("MTD(%)", 100, 10),
                token("产品A", 10, 50),
                token("1.00%", 100, 50),
            ]

    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    admin = User(username="admin", password_hash="hash", role="admin")
    session.add(admin)
    session.flush()
    production_run = UpdateRun(
        operator_id=admin.id,
        cutoff_date=date(2026, 7, 17),
        status="completed",
    )
    session.add(production_run)
    session.flush()
    production_item = RunItem(
        run_id=production_run.id,
        excel_row=2,
        metric_values={"mtd": "0.01"},
        metric_status={"mtd": "extracted"},
        original_values={"product_name": "产品A"},
    )
    session.add(production_item)
    image = tmp_path / "samples" / "sample.png"
    image.parent.mkdir()
    image.write_bytes(b"sample")
    sample_run = OcrRegressionRun(requested_by=admin.id)
    session.add(sample_run)
    session.flush()
    session.add_all(
        [
            OcrRegressionSample(
                image_path=str(image),
                image_sha256=hashlib.sha256(b"sample").hexdigest(),
                source_label="管理员复核案例",
                excel_product_name="产品A",
                candidate_names=["产品A"],
                expected_product_code="P001",
                expected_metric_values={"mtd": "0.01"},
                expected_metric_status={"mtd": "extracted"},
                note="通过",
                is_active=True,
            ),
            OcrRegressionSample(
                image_path=str(image),
                image_sha256=hashlib.sha256(b"sample").hexdigest(),
                source_label="管理员复核案例",
                excel_product_name="产品A",
                candidate_names=["产品A"],
                expected_product_code="P001",
                expected_metric_values={"mtd": "0.02"},
                expected_metric_status={"mtd": "extracted"},
                note="故意不一致",
                is_active=True,
            ),
        ]
    )
    session.commit()
    before = dict(production_item.metric_values)

    run_regression(session, sample_run.id, samples_root=tmp_path / "samples", ocr_service=FakeOCR())

    session.refresh(sample_run)
    results = session.query(OcrRegressionResult).filter_by(run_id=sample_run.id).all()
    assert sample_run.status == "completed"
    assert sample_run.total_count == 2
    assert sample_run.passed_count == 1
    assert sample_run.failed_count == 1
    assert {result.outcome for result in results} == {"passed", "value_mismatch"}
    assert production_item.metric_values == before


def test_find_image_row_matches_unique_truncated_chinese_name() -> None:
    item_name = "浑瑾岳桐金选1号B"
    row = OCRMetricRow(
        product_name="浑瑾岳桐B1I1",
        product_code=None,
        metrics={"weekly": Decimal("-0.0414")},
        confidence=0.99,
    )

    assert _find_image_row(item_name, [row], [], [item_name]) == row


def test_find_image_row_matches_a_unique_equal_chinese_prefix() -> None:
    item_name = "仁桥金选泽源5B"
    row = OCRMetricRow(
        product_name="仁桥金选泽源5B1]",
        product_code=None,
        metrics={"weekly": Decimal("0.01")},
        confidence=0.99,
    )

    assert _find_image_row(item_name, [row], [], [item_name]) == row


def test_find_image_row_rejects_ambiguous_truncated_chinese_name() -> None:
    row = OCRMetricRow(
        product_name="浑瑾岳桐B1I1",
        product_code=None,
        metrics={"weekly": Decimal("-0.0414")},
        confidence=0.99,
    )

    assert _find_image_row(
        "浑瑾岳桐金选1号B",
        [row],
        [],
        ["浑瑾岳桐金选1号B", "浑瑾岳桐金选2号B"],
    ) is None


def test_find_image_row_rejects_an_equal_chinese_prefix_with_two_excel_candidates() -> None:
    row = OCRMetricRow(
        product_name="聚鸣金选高山8号B1]",
        product_code=None,
        metrics={"weekly": Decimal("0.01")},
        confidence=0.99,
    )

    assert _find_image_row(
        "聚鸣金选高山8号B",
        [row],
        [],
        ["聚鸣金选高山8号B", "聚鸣金选高山3号B"],
    ) is None


def test_find_image_row_rejects_a_prefix_with_an_active_catalog_sibling() -> None:
    row = OCRMetricRow(
        product_name="仁桥金选泽源5B1]",
        product_code=None,
        metrics={"weekly": Decimal("0.01")},
        confidence=0.99,
    )
    sibling = Product(
        product_name="仁桥金选泽源6B",
        product_code="private-bridge-6b",
        product_type="private",
    )

    assert _find_image_row("仁桥金选泽源5B", [row], [sibling], ["仁桥金选泽源5B"]) is None


def test_find_image_row_rejects_truncated_name_with_a_different_product_code() -> None:
    item_name = "浑瑾岳桐金选1号B"
    row = OCRMetricRow(
        product_name="浑瑾岳桐B1I1",
        product_code="P999",
        metrics={"weekly": Decimal("-0.0414")},
        confidence=0.99,
    )
    other_product = Product(product_name="其他产品", product_code="P999", product_type="private")

    assert _find_image_row(item_name, [row], [other_product], [item_name]) is None


def test_find_image_row_allows_duplicate_excel_rows_for_one_truncated_name() -> None:
    item_name = "浑瑾岳桐金选1号B"
    row = OCRMetricRow(
        product_name="浑瑾岳桐B1I1",
        product_code=None,
        metrics={"weekly": Decimal("-0.0414")},
        confidence=0.99,
    )

    assert _find_image_row(item_name, [row], [], [item_name, item_name]) == row


def test_process_run_resolves_and_persists_unique_public_product() -> None:
    class CapturingAdapter:
        updates: dict[int, dict[str, Decimal]]

        def apply_updates(self, input_path, output_path, updates, stale) -> None:
            self.updates = updates

    class ResolvingProvider:
        def resolve_by_name(self, product_name: str) -> PublicFundRecord | None:
            assert product_name == "易方达环保主题灵活配置混合A"
            return PublicFundRecord("001856", "易方达环保主题混合A")

        def fetch_history(
            self,
            product_code: str,
            start_date: date | None = None,
        ) -> list[NavPoint]:
            assert product_code == "001856"
            assert start_date is None
            return [
                NavPoint(date(2025, 7, 10), Decimal("100"), "fixture"),
                NavPoint(date(2026, 7, 10), Decimal("110"), "fixture"),
                NavPoint(date(2026, 7, 17), Decimal("111"), "fixture"),
            ]

    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    admin = User(username="admin", password_hash="hash", role="admin")
    session.add(admin)
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
    item = RunItem(
        run_id=run.id,
        excel_row=7,
        original_values={"product_name": "易方达环保主题灵活配置混合A"},
    )
    session.add(item)
    session.commit()
    adapter = CapturingAdapter()

    process_run(session, run.id, provider=ResolvingProvider(), adapter=adapter, actor_id=admin.id)

    product = session.query(Product).filter_by(product_code="001856").one()
    assert product.product_name == "易方达环保主题混合A"
    assert product.product_type == "public"
    assert product.historical_names == ["易方达环保主题灵活配置混合A"]
    assert item.product_id == product.id
    assert item.match_source == "public_provider"
    assert adapter.updates[item.excel_row]["weekly"] == Decimal("0.009090909090909090909090909")
    assert session.query(AuditLog).filter_by(action="resolve_public_product").count() == 1


def test_process_run_fetches_public_history_since_latest_observation() -> None:
    class CapturingAdapter:
        def apply_updates(self, input_path, output_path, updates, stale) -> None:
            pass

    class CapturingProvider:
        requested_start_date: date | None = None

        def fetch_history(
            self,
            product_code: str,
            start_date: date | None = None,
        ) -> list[NavPoint]:
            assert product_code == "001856"
            self.requested_start_date = start_date
            return [NavPoint(date(2026, 7, 17), Decimal("111"), "fixture")]

    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    admin = User(username="admin", password_hash="hash", role="admin")
    product = Product(
        product_name="易方达环保主题混合A",
        product_code="001856",
        product_type="public",
    )
    session.add_all([admin, product])
    session.flush()
    run = UpdateRun(operator_id=admin.id, cutoff_date=date(2026, 7, 17), status="uploaded")
    session.add(run)
    session.flush()
    session.add_all(
        [
            RunFile(
                run_id=run.id,
                file_type="workbook",
                original_name="template.xlsx",
                storage_path="/tmp/template.xlsx",
                sha256="0" * 64,
            ),
            RunItem(
                run_id=run.id,
                excel_row=7,
                original_values={"product_name": "易方达环保主题混合A"},
            ),
            NavObservation(
                product_id=product.id,
                nav_date=date(2026, 7, 10),
                cumulative_nav=Decimal("110"),
                source_kind="eastmoney",
                source_ref="fixture",
            ),
        ]
    )
    session.commit()
    provider = CapturingProvider()

    process_run(
        session,
        run.id,
        provider=provider,
        ocr_service=object(),
        adapter=CapturingAdapter(),
    )

    assert provider.requested_start_date == date(2026, 7, 10)
    assert (
        session.query(NavObservation)
        .filter_by(product_id=product.id, nav_date=date(2026, 7, 17), source_kind="eastmoney")
        .count()
        == 1
    )


def test_lock_run_item_requests_row_locks() -> None:
    run = object()
    item = object()

    class RecordingSession:
        def __init__(self) -> None:
            self.statements = []
            self.results = [run, item]

        def scalar(self, statement):
            self.statements.append(statement)
            return self.results.pop(0)

    session = RecordingSession()

    assert lock_run_item(session, run_id=3, item_id=9) == (run, item)
    assert len(session.statements) == 2
    assert all(statement._for_update_arg is not None for statement in session.statements)
