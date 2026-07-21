from __future__ import annotations

import hashlib
import json
from pathlib import Path

from PIL import Image

from .table_parser import MetricCellEvidence, OCRMetricRow


def _cell_payload(cell: MetricCellEvidence) -> dict[str, object]:
    return {
        "text": cell.text,
        "confidence": cell.confidence,
        "box": [[float(x), float(y)] for x, y in cell.box],
    }


def merge_metric_passes(
    first: OCRMetricRow,
    second: OCRMetricRow | None,
) -> tuple[OCRMetricRow, dict[str, object]]:
    metrics = dict(first.metrics)
    blank_metrics = set(first.blank_metrics)
    selected_evidence = dict(first.metric_evidence)
    evidence_metrics: dict[str, dict[str, object]] = {}
    selected_pass: dict[str, int | None] = {}
    keys = set(first.metric_evidence) | set(first.metrics) | set(first.blank_metrics)
    if second is not None:
        keys |= set(second.metric_evidence) | set(second.metrics) | set(second.blank_metrics)

    for key in sorted(keys):
        passes: list[dict[str, object]] = []
        first_cell = first.metric_evidence.get(key)
        second_cell = second.metric_evidence.get(key) if second is not None else None
        if first_cell is not None:
            passes.append({"pass": 1, **_cell_payload(first_cell)})
        if second_cell is not None:
            passes.append({"pass": 2, **_cell_payload(second_cell)})
        selected = 1 if key in first.metrics else None
        if second is not None and key in second.metrics and key not in first.metrics:
            metrics[key] = second.metrics[key]
            blank_metrics.discard(key)
            selected_evidence[key] = second_cell or selected_evidence.get(key)
            selected = 2
        elif key in first.blank_metrics and second is not None and key in second.blank_metrics:
            selected = 2
        elif key in first.blank_metrics and key not in metrics:
            blank_metrics.add(key)
        selected_pass[key] = selected
        evidence_metrics[key] = {"passes": passes, "selected_pass": selected}

    return (
        OCRMetricRow(
            product_name=first.product_name,
            product_code=first.product_code or (second.product_code if second else None),
            metrics=metrics,
            confidence=min(first.confidence, second.confidence) if second else first.confidence,
            blank_metrics=frozenset(blank_metrics),
            metric_evidence=selected_evidence,
        ),
        {"metrics": evidence_metrics, "selected_pass": selected_pass},
    )


def metric_row_evidence(
    row: OCRMetricRow,
    *,
    pass_number: int,
    image_name: str,
    image_sha256: str,
) -> dict[str, object]:
    return {
        "image_name": image_name,
        "image_sha256": image_sha256,
        "pass": pass_number,
        "metrics": {
            key: {"text": cell.text, "confidence": cell.confidence, "box": cell.box}
            for key, cell in row.metric_evidence.items()
        },
    }


def crop_box(
    image: str | Path,
    box: tuple[tuple[float, float], ...],
    destination_root: Path,
) -> Path:
    if not box:
        raise ValueError("截图证据缺少边界框")
    source_path = Path(image).resolve()
    destination_root = destination_root.resolve()
    destination_root.mkdir(parents=True, exist_ok=True)
    with Image.open(source_path) as source:
        width, height = source.size
        left = max(0, min(width, int(min(point[0] for point in box))))
        top = max(0, min(height, int(min(point[1] for point in box))))
        right = max(0, min(width, int(max(point[0] for point in box))))
        bottom = max(0, min(height, int(max(point[1] for point in box))))
        if right <= left or bottom <= top:
            raise ValueError("截图证据边界框无效")
        key = json.dumps([source_path.stat().st_mtime_ns, box], ensure_ascii=True)
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:20]
        target = destination_root / f"evidence-{digest}.png"
        if not target.exists():
            source.crop((left, top, right, bottom)).convert("RGB").save(target, format="PNG")
    return target
