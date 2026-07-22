import cv2
import numpy as np

from app.ocr import engine
from app.ocr.engine import OCRService, OCRToken


def token(text: str, *, top: float, confidence: float = 0.99) -> OCRToken:
    return OCRToken(
        text=text,
        box=((10.0, top), (30.0, top), (30.0, top + 10), (10.0, top + 10)),
        confidence=confidence,
    )


def test_recognize_tiled_offsets_tokens_and_keeps_best_overlap_token(monkeypatch) -> None:
    image = np.zeros((6000, 20, 3), dtype=np.uint8)
    service = OCRService()
    starts = iter([0, 2472, 4944])

    def recognize(_crop):
        start = next(starts)
        if start == 0:
            return [token("重复", top=2472, confidence=0.80), token("首段", top=0)]
        if start == 2472:
            return [token("重复", top=0), token("中段", top=10)]
        return [token("末段", top=10)]

    monkeypatch.setattr(service, "recognize", recognize)

    result = service.recognize_tiled(image, tile_height=2600, overlap=128)

    assert [(item.text, item.top, item.confidence) for item in result] == [
        ("首段", 0.0, 0.99),
        ("重复", 2472.0, 0.99),
        ("中段", 2482.0, 0.99),
        ("末段", 4954.0, 0.99),
    ]


def test_recognize_tiled_uses_smaller_default_tiles_for_dense_reports(monkeypatch) -> None:
    image = np.zeros((5000, 20, 3), dtype=np.uint8)
    service = OCRService()
    tile_heights: list[int] = []

    def recognize(crop):
        tile_heights.append(crop.shape[0])
        return []

    monkeypatch.setattr(service, "recognize", recognize)

    service.recognize_tiled(image)

    assert tile_heights == [1600, 1600, 1600, 584]


def test_recognize_tiled_dense_overlaps_rows_after_report_header(monkeypatch) -> None:
    service = OCRService()
    calls: list[tuple[int, int]] = []

    def recognize_tiled(_image, *, tile_height: int, overlap: int):
        calls.append((tile_height, overlap))
        return []

    monkeypatch.setattr(service, "recognize_tiled", recognize_tiled)

    service.recognize_tiled_dense(np.zeros((1000, 20, 3), dtype=np.uint8))

    assert calls == [(800, 400)]


def test_detect_source_blank_tokens_keeps_an_isolated_dash() -> None:
    image = np.full((80, 200, 3), 255, dtype=np.uint8)
    cv2.line(image, (110, 42), (126, 42), (0, 0, 0), 2)

    detected = engine._detect_source_blank_tokens(image, [])

    assert len(detected) == 1
    assert detected[0].text == "-"
    assert detected[0].left == 109.0
    assert detected[0].top == 41.0


def test_detect_source_blank_tokens_ignores_dash_inside_recognized_text() -> None:
    image = np.full((80, 200, 3), 255, dtype=np.uint8)
    cv2.line(image, (110, 42), (126, 42), (0, 0, 0), 2)
    existing = [
        OCRToken(
            "-12.95%",
            ((106.0, 38.0), (150.0, 38.0), (150.0, 52.0), (106.0, 52.0)),
            0.99,
        )
    ]

    assert engine._detect_source_blank_tokens(image, existing) == []
