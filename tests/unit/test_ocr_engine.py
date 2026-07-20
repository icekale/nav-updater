import numpy as np

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
