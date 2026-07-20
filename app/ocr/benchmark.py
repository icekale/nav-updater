from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path

from ..domain.matching import is_unique_ocr_name_match
from .engine import OCRToken
from .table_parser import METRIC_KEYS, OCRMetricRow, extract_metric_rows


class BenchmarkFormatError(ValueError):
    pass


class BenchmarkSourceError(ValueError):
    pass


@dataclass(frozen=True)
class BenchmarkCase:
    image: str
    sha256: str
    product_name: str
    metrics: Mapping[str, Decimal | None]
    candidate_names: tuple[str, ...] = ()


@dataclass(frozen=True)
class BenchmarkCaseResult:
    image: str
    product_name: str
    product_matched: bool
    correct_fields: int
    missed_fields: int
    wrong_fields: int
    wrong_column_fields: int
    field_outcomes: Mapping[str, str]
    expected_blank_metrics: frozenset[str] = frozenset()


@dataclass(frozen=True)
class BenchmarkReport:
    results: tuple[BenchmarkCaseResult, ...]

    @property
    def product_matches(self) -> int:
        return sum(result.product_matched for result in self.results)

    @property
    def correct_fields(self) -> int:
        return sum(result.correct_fields for result in self.results)

    @property
    def missed_fields(self) -> int:
        return sum(result.missed_fields for result in self.results)

    @property
    def wrong_fields(self) -> int:
        return sum(result.wrong_fields for result in self.results)

    @property
    def wrong_column_fields(self) -> int:
        return sum(result.wrong_column_fields for result in self.results)

    @property
    def total_fields(self) -> int:
        return self.correct_fields + self.missed_fields + self.wrong_fields

    @property
    def source_blanks(self) -> int:
        return sum(len(result.expected_blank_metrics) for result in self.results)

    @property
    def correct_source_blanks(self) -> int:
        return sum(
            result.field_outcomes.get(metric) == "correct"
            for result in self.results
            for metric in result.expected_blank_metrics
        )

    @property
    def source_blank_recall(self) -> float:
        return _rate(self.correct_source_blanks, self.source_blanks)


def evaluate_cases(
    cases: Iterable[BenchmarkCase], rows_by_image: Mapping[str, list[OCRMetricRow]]
) -> BenchmarkReport:
    results = []
    for case in cases:
        row = _matched_row(
            case.product_name,
            rows_by_image.get(case.image, []),
            case.candidate_names or (case.product_name,),
        )
        if row is None:
            results.append(
                BenchmarkCaseResult(
                    image=case.image,
                    product_name=case.product_name,
                    product_matched=False,
                    correct_fields=0,
                    missed_fields=len(case.metrics),
                    wrong_fields=0,
                    wrong_column_fields=0,
                    field_outcomes=dict.fromkeys(case.metrics, "product_unmatched"),
                    expected_blank_metrics=frozenset(
                        metric for metric, expected in case.metrics.items() if expected is None
                    ),
                )
            )
            continue

        correct_fields = 0
        missed_fields = 0
        wrong_fields = 0
        wrong_column_fields = 0
        field_outcomes: dict[str, str] = {}
        for metric, expected in case.metrics.items():
            actual = row.metrics.get(metric)
            if expected is None:
                if metric in row.blank_metrics:
                    correct_fields += 1
                    field_outcomes[metric] = "correct"
                elif actual is None:
                    missed_fields += 1
                    field_outcomes[metric] = "missed"
                else:
                    wrong_fields += 1
                    field_outcomes[metric] = "wrong"
                continue
            if actual == expected:
                correct_fields += 1
                field_outcomes[metric] = "correct"
            elif actual is None:
                missed_fields += 1
                field_outcomes[metric] = "missed"
            else:
                wrong_fields += 1
                if _is_wrong_column(expected, metric, row.metrics):
                    wrong_column_fields += 1
                    field_outcomes[metric] = "wrong_column"
                else:
                    field_outcomes[metric] = "wrong"
        results.append(
            BenchmarkCaseResult(
                image=case.image,
                product_name=case.product_name,
                product_matched=True,
                correct_fields=correct_fields,
                missed_fields=missed_fields,
                wrong_fields=wrong_fields,
                wrong_column_fields=wrong_column_fields,
                field_outcomes=field_outcomes,
                expected_blank_metrics=frozenset(
                    metric for metric, expected in case.metrics.items() if expected is None
                ),
            )
        )
    return BenchmarkReport(tuple(results))


def load_cases(path: str | Path) -> list[BenchmarkCase]:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BenchmarkFormatError(f"unable to load labels: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("version") != 1:
        raise BenchmarkFormatError("labels must be a version 1 JSON object")
    raw_cases = payload.get("cases")
    if not isinstance(raw_cases, list) or not raw_cases:
        raise BenchmarkFormatError("labels must contain at least one case")

    candidate_names = _parse_candidate_names(payload.get("candidate_names"))
    cases = []
    for index, raw_case in enumerate(raw_cases, start=1):
        if not isinstance(raw_case, dict):
            raise BenchmarkFormatError(f"case {index} must be an object")
        image = raw_case.get("image")
        sha256 = raw_case.get("sha256")
        product_name = raw_case.get("product_name")
        metrics = raw_case.get("metrics")
        if not isinstance(image, str) or not image.strip():
            raise BenchmarkFormatError(f"case {index} has an invalid image")
        if not isinstance(sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", sha256):
            raise BenchmarkFormatError(f"case {index} has an invalid SHA-256")
        if not isinstance(product_name, str) or not product_name.strip():
            raise BenchmarkFormatError(f"case {index} has an invalid product_name")
        cases.append(
            BenchmarkCase(
                image=image,
                sha256=sha256,
                product_name=product_name,
                metrics=_parse_metrics(metrics, index),
                candidate_names=candidate_names,
            )
        )
    return cases


def report_as_dict(report: BenchmarkReport) -> dict[str, object]:
    return {
        "totals": {
            "products": len(report.results),
            "product_matches": report.product_matches,
            "product_match_rate": _rate(report.product_matches, len(report.results)),
            "fields": report.total_fields,
            "correct_fields": report.correct_fields,
            "field_accuracy": _rate(report.correct_fields, report.total_fields),
            "missed_fields": report.missed_fields,
            "missed_field_rate": _rate(report.missed_fields, report.total_fields),
            "wrong_fields": report.wrong_fields,
            "wrong_column_fields": report.wrong_column_fields,
            "wrong_column_rate": _rate(report.wrong_column_fields, report.total_fields),
            "source_blanks": report.source_blanks,
            "correct_source_blanks": report.correct_source_blanks,
            "source_blank_recall": report.source_blank_recall,
        },
        "cases": [
            {
                "image": result.image,
                "product_name": result.product_name,
                "product_matched": result.product_matched,
                "correct_fields": result.correct_fields,
                "missed_fields": result.missed_fields,
                "wrong_fields": result.wrong_fields,
                "wrong_column_fields": result.wrong_column_fields,
                "field_outcomes": dict(result.field_outcomes),
            }
            for result in report.results
        ],
    }


def render_markdown(report: BenchmarkReport) -> str:
    totals = report_as_dict(report)["totals"]
    assert isinstance(totals, dict)
    lines = [
        "# OCR 基准结果",
        "",
        "## 汇总",
        "",
        (
            f"- 产品匹配率：{totals['product_match_rate']:.2%}"
            f"（{totals['product_matches']} / {totals['products']}）"
        ),
        (
            f"- 字段准确率：{totals['field_accuracy']:.2%}"
            f"（{totals['correct_fields']} / {totals['fields']}）"
        ),
        (
            f"- 漏识别率：{totals['missed_field_rate']:.2%}"
            f"（{totals['missed_fields']} / {totals['fields']}）"
        ),
        (
            f"- 错列率：{totals['wrong_column_rate']:.2%}"
            f"（{totals['wrong_column_fields']} / {totals['fields']}）"
        ),
        (
            f"- 源空值识别率：{totals['source_blank_recall']:.2%}"
            f"（{totals['correct_source_blanks']} / {totals['source_blanks']}）"
        ),
        "",
        "## 分图片",
        "",
        "| 图片 | 产品 | 匹配 | 正确字段 | 漏识别 | 错误 | 错列 |",
        "| --- | --- | --- | ---: | ---: | ---: | ---: |",
    ]
    for result in report.results:
        lines.append(
            (
                "| {image} | {product_name} | {matched} | {correct} | {missed} | "
                "{wrong} | {wrong_column} |"
            ).format(
                image=result.image,
                product_name=result.product_name,
                matched="是" if result.product_matched else "否",
                correct=result.correct_fields,
                missed=result.missed_fields,
                wrong=result.wrong_fields,
                wrong_column=result.wrong_column_fields,
            )
        )
    return "\n".join(lines) + "\n"


def verify_source_hashes(
    cases: Iterable[BenchmarkCase], images_root: str | Path
) -> dict[str, Path]:
    root = Path(images_root).resolve()
    verified: dict[str, Path] = {}
    expected_hashes: dict[str, str] = {}
    for case in cases:
        previous = expected_hashes.setdefault(case.image, case.sha256)
        if previous != case.sha256:
            raise BenchmarkSourceError(f"conflicting SHA-256 values for {case.image}")
        if case.image in verified:
            continue
        source = (root / case.image).resolve()
        if root not in source.parents or not source.is_file():
            raise BenchmarkSourceError(f"source image is missing: {case.image}")
        if _sha256_file(source) != case.sha256:
            raise BenchmarkSourceError(f"source image SHA-256 does not match: {case.image}")
        verified[case.image] = source
    return verified


def run_benchmark(
    cases: Iterable[BenchmarkCase],
    sources: Mapping[str, Path],
    recognize: Callable[[Path], Iterable[OCRToken]],
) -> BenchmarkReport:
    frozen_cases = tuple(cases)
    rows_by_image = {
        image: extract_metric_rows(recognize(path)) for image, path in sources.items()
    }
    return evaluate_cases(frozen_cases, rows_by_image)


def write_report(report: BenchmarkReport, output_dir: str | Path) -> None:
    output = Path(output_dir)
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise BenchmarkSourceError(f"output directory is not empty: {output}")
    output.mkdir(parents=True, exist_ok=True)
    (output / "details.json").write_text(
        json.dumps(report_as_dict(report), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (output / "summary.md").write_text(render_markdown(report), encoding="utf-8")


def _matched_row(
    product_name: str, rows: Iterable[OCRMetricRow], candidate_names: Iterable[str]
) -> OCRMetricRow | None:
    matches = [
        row
        for row in rows
        if is_unique_ocr_name_match(product_name, row.product_name, candidate_names)
    ]
    return matches[0] if len(matches) == 1 else None


def _is_wrong_column(expected: Decimal, metric: str, values: Mapping[str, Decimal]) -> bool:
    return sum(key != metric and value == expected for key, value in values.items()) == 1


def _parse_metrics(raw_metrics: object, index: int) -> dict[str, Decimal | None]:
    if not isinstance(raw_metrics, dict) or set(raw_metrics) != METRIC_KEYS:
        raise BenchmarkFormatError(f"case {index} must contain exactly the 12 metrics")
    metrics: dict[str, Decimal | None] = {}
    for metric, raw_value in raw_metrics.items():
        if raw_value is None:
            metrics[metric] = None
            continue
        if not isinstance(raw_value, str):
            raise BenchmarkFormatError(
                f"case {index} metric {metric} must be a decimal string or null"
            )
        try:
            value = Decimal(raw_value)
        except InvalidOperation as exc:
            raise BenchmarkFormatError(f"case {index} metric {metric} is invalid") from exc
        if not value.is_finite():
            raise BenchmarkFormatError(f"case {index} metric {metric} is invalid")
        metrics[metric] = value
    return metrics


def _parse_candidate_names(raw_candidates: object) -> tuple[str, ...]:
    if raw_candidates is None:
        return ()
    if not isinstance(raw_candidates, list) or not all(
        isinstance(name, str) and name.strip() for name in raw_candidates
    ):
        raise BenchmarkFormatError("candidate_names must be a list of non-empty strings")
    return tuple(name.strip() for name in raw_candidates)


def _rate(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()
