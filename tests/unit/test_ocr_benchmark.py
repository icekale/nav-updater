import hashlib
import json
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

import pytest

from app.ocr.engine import OCRToken
from app.ocr.table_parser import METRIC_KEYS, OCRMetricRow


def test_evaluate_cases_counts_numeric_and_confirmed_blank_fields() -> None:
    from app.ocr.benchmark import BenchmarkCase, evaluate_cases

    case = BenchmarkCase(
        image="report.png",
        sha256="a" * 64,
        product_name="产品A",
        metrics={"weekly": Decimal("0.01"), "mtd": None},
    )
    row = OCRMetricRow(
        product_name="产品A",
        product_code=None,
        metrics={"weekly": Decimal("0.01")},
        confidence=0.99,
        blank_metrics=frozenset({"mtd"}),
    )

    report = evaluate_cases([case], {"report.png": [row]})

    assert report.product_matches == 1
    assert report.correct_fields == 2
    assert report.missed_fields == 0
    assert report.wrong_fields == 0
    assert report.wrong_column_fields == 0


def test_benchmark_report_exposes_confirmed_source_blank_recall() -> None:
    from app.ocr.benchmark import BenchmarkCase, evaluate_cases, render_markdown, report_as_dict

    case = BenchmarkCase(
        image="report.png",
        sha256="a" * 64,
        product_name="产品A",
        metrics={"weekly": Decimal("0.01"), "mtd": None},
    )
    row = OCRMetricRow(
        product_name="产品A",
        product_code=None,
        metrics={"weekly": Decimal("0.01")},
        confidence=0.99,
        blank_metrics=frozenset({"mtd"}),
    )

    report = evaluate_cases([case], {"report.png": [row]})
    totals = report_as_dict(report)["totals"]

    assert totals["source_blanks"] == 1
    assert totals["correct_source_blanks"] == 1
    assert totals["source_blank_recall"] == 1.0
    assert "源空值识别率" in render_markdown(report)


def test_evaluate_cases_flags_value_found_in_a_different_metric_column() -> None:
    from app.ocr.benchmark import BenchmarkCase, evaluate_cases

    case = BenchmarkCase(
        image="report.png",
        sha256="a" * 64,
        product_name="产品A",
        metrics={"weekly": Decimal("0.01"), "mtd": Decimal("0.02")},
    )
    row = OCRMetricRow(
        product_name="产品A",
        product_code=None,
        metrics={"weekly": Decimal("0.02"), "mtd": Decimal("0.01")},
        confidence=0.99,
    )

    report = evaluate_cases([case], {"report.png": [row]})

    assert report.product_matches == 1
    assert report.correct_fields == 0
    assert report.wrong_fields == 2
    assert report.wrong_column_fields == 2


def test_load_cases_rejects_incomplete_metrics(tmp_path: Path) -> None:
    from app.ocr.benchmark import BenchmarkFormatError, load_cases

    labels = tmp_path / "labels.json"
    labels.write_text(
        json.dumps(
            {
                "version": 1,
                "cases": [
                    {
                        "image": "report.png",
                        "sha256": "a" * 64,
                        "product_name": "产品A",
                        "metrics": {"weekly": "0.01"},
                    }
                ],
            }
        )
    )

    with pytest.raises(BenchmarkFormatError, match="metrics"):
        load_cases(labels)


def test_render_markdown_includes_rates_and_wrong_column_count() -> None:
    from app.ocr.benchmark import BenchmarkCase, evaluate_cases, render_markdown

    metrics = {metric: Decimal("0.01") for metric in METRIC_KEYS}
    case = BenchmarkCase("report.png", "a" * 64, "产品A", metrics)
    row = OCRMetricRow("产品A", None, metrics, 0.99)

    markdown = render_markdown(evaluate_cases([case], {"report.png": [row]}))

    assert "产品匹配率" in markdown
    assert "错列率" in markdown


def test_verify_source_hashes_rejects_changed_image(tmp_path: Path) -> None:
    from app.ocr.benchmark import BenchmarkCase, BenchmarkSourceError, verify_source_hashes

    image = tmp_path / "report.png"
    image.write_bytes(b"changed")
    case = BenchmarkCase(
        image="report.png",
        sha256="a" * 64,
        product_name="产品A",
        metrics={metric: Decimal("0") for metric in METRIC_KEYS},
    )

    with pytest.raises(BenchmarkSourceError, match="SHA-256"):
        verify_source_hashes([case], tmp_path)


def test_run_benchmark_writes_reports_without_changing_source_image(tmp_path: Path) -> None:
    from app.ocr.benchmark import (
        BenchmarkCase,
        run_benchmark,
        verify_source_hashes,
        write_report,
    )

    source = tmp_path / "report.png"
    original = b"source image"
    source.write_bytes(original)
    case = BenchmarkCase(
        image="report.png",
        sha256=hashlib.sha256(original).hexdigest(),
        product_name="产品A",
        metrics={metric: Decimal("0") for metric in METRIC_KEYS},
    )
    def token(text: str, left: float, top: float) -> OCRToken:
        return OCRToken(
            text,
            ((left, top), (left + 50, top), (left + 50, top + 20), (left, top + 20)),
            0.99,
        )

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
    values = ["0.00%"] * 10 + ["0", "0.00%"]
    tokens = [
        token("产品名称", 10, 10),
        *(token(header, 100 + index * 100, 10) for index, header in enumerate(headers)),
        token("产品A", 10, 50),
        *(token(value, 100 + index * 100, 50) for index, value in enumerate(values)),
    ]

    report = run_benchmark([case], verify_source_hashes([case], tmp_path), lambda path: tokens)
    output = tmp_path / "report"
    write_report(report, output)

    assert source.read_bytes() == original
    assert (output / "summary.md").is_file()
    assert json.loads((output / "details.json").read_text()) == {
        "totals": {
            "products": 1,
            "product_matches": 1,
            "product_match_rate": 1.0,
            "fields": 12,
            "correct_fields": 12,
            "field_accuracy": 1.0,
            "missed_fields": 0,
            "missed_field_rate": 0.0,
            "wrong_fields": 0,
            "wrong_column_fields": 0,
            "wrong_column_rate": 0.0,
            "source_blanks": 0,
            "correct_source_blanks": 0,
            "source_blank_recall": 0.0,
        },
        "cases": [
            {
                "image": "report.png",
                "product_name": "产品A",
                "product_matched": True,
                "correct_fields": 12,
                "missed_fields": 0,
                "wrong_fields": 0,
                "wrong_column_fields": 0,
                "field_outcomes": {metric: "correct" for metric in METRIC_KEYS},
            }
        ],
    }


def test_benchmark_script_runs_directly_from_the_project_root() -> None:
    project_root = Path(__file__).parents[2]

    result = subprocess.run(
        [sys.executable, "scripts/run_ocr_benchmark.py", "--help"],
        cwd=project_root,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert "--labels" in result.stdout
