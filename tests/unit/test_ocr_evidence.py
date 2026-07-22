from decimal import Decimal
from pathlib import Path

from PIL import Image

from app.ocr.engine import OCRToken
from app.ocr.evidence import crop_box, merge_metric_passes
from app.ocr.table_parser import MetricCellEvidence, OCRMetricRow, extract_metric_rows


def _token(text: str, left: float, top: float, confidence: float = 0.99) -> OCRToken:
    return OCRToken(
        text,
        ((left, top), (left + 50, top), (left + 50, top + 20), (left, top + 20)),
        confidence,
    )


def test_metric_row_retains_metric_cell_evidence() -> None:
    rows = extract_metric_rows(
        [
            _token("产品名称", 10, 10),
            _token("MTD(%)", 100, 10),
            _token("浑瑾岳桐金选1号B", 10, 50),
            _token("-6.33", 100, 50),
        ]
    )

    evidence = rows[0].metric_evidence["mtd"]
    assert evidence.text == "-6.33"
    assert evidence.confidence == 0.99
    assert len(evidence.box) == 4


def test_merge_metric_passes_prefers_second_pass_only_for_missing_value() -> None:
    first = OCRMetricRow(
        product_name="产品A",
        product_code=None,
        metrics={},
        confidence=0.5,
        blank_metrics=frozenset({"mtd"}),
        metric_evidence={
            "mtd": MetricCellEvidence("-", 1.0, ((10, 10), (20, 10), (20, 20), (10, 20)))
        },
    )
    second = OCRMetricRow(
        product_name="产品A",
        product_code=None,
        metrics={"mtd": Decimal("-0.0633")},
        confidence=0.99,
        metric_evidence={
            "mtd": MetricCellEvidence(
                "-6.33", 0.99, ((10, 10), (40, 10), (40, 20), (10, 20))
            )
        },
    )

    merged, evidence = merge_metric_passes(first, second)

    assert merged.metrics == {"mtd": Decimal("-0.0633")}
    assert merged.blank_metrics == frozenset()
    assert evidence["metrics"]["mtd"]["selected_pass"] == 2


def test_merge_metric_passes_does_not_keep_blank_when_second_pass_is_missing() -> None:
    first = OCRMetricRow(
        product_name="产品A",
        product_code=None,
        metrics={},
        confidence=0.5,
        blank_metrics=frozenset({"mtd"}),
        metric_evidence={
            "mtd": MetricCellEvidence("-", 1.0, ((10, 10), (20, 10), (20, 20), (10, 20)))
        },
    )
    second = OCRMetricRow(
        product_name="产品A",
        product_code=None,
        metrics={},
        confidence=0.99,
    )

    merged, _ = merge_metric_passes(first, second, second_attempted=True)

    assert merged.blank_metrics == frozenset()


def test_merge_metric_passes_requires_both_passes_to_confirm_a_blank() -> None:
    first = OCRMetricRow(
        product_name="产品A",
        product_code=None,
        metrics={},
        confidence=0.5,
    )
    second = OCRMetricRow(
        product_name="产品A",
        product_code=None,
        metrics={},
        confidence=0.99,
        blank_metrics=frozenset({"mtd"}),
    )

    merged, _ = merge_metric_passes(first, second)

    assert merged.blank_metrics == frozenset()


def test_merge_metric_passes_preserves_second_pass_label_for_second_only_row() -> None:
    row = OCRMetricRow(
        product_name="产品A",
        product_code=None,
        metrics={"mtd": Decimal("-0.0633")},
        confidence=0.99,
        metric_evidence={
            "mtd": MetricCellEvidence(
                "-6.33", 0.99, ((10, 10), (40, 10), (40, 20), (10, 20))
            )
        },
    )

    _, evidence = merge_metric_passes(row, None, first_pass=2, allow_single_pass_blank=False)

    assert evidence["metrics"]["mtd"]["passes"][0]["pass"] == 2


def test_crop_box_clamps_coordinates_to_image_bounds(tmp_path: Path) -> None:
    source = tmp_path / "source.png"
    Image.new("RGB", (20, 20), "white").save(source)

    cropped = crop_box(
        source,
        ((-5, -5), (30, -5), (30, 30), (-5, 30)),
        tmp_path / "crops",
    )

    assert cropped.exists()
    assert Image.open(cropped).size == (20, 20)


def test_crop_box_separates_evidence_for_different_image_hashes(tmp_path: Path) -> None:
    source = tmp_path / "source.png"
    Image.new("RGB", (20, 20), "white").save(source)
    box = ((0, 0), (10, 0), (10, 10), (0, 10))

    first = crop_box(source, box, tmp_path / "crops", image_sha256="a" * 64)
    second = crop_box(source, box, tmp_path / "crops", image_sha256="b" * 64)

    assert first != second
