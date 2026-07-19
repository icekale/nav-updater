from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
from rapidocr_onnxruntime import RapidOCR


@dataclass(frozen=True)
class OCRToken:
    text: str
    box: tuple[tuple[float, float], ...]
    confidence: float

    @property
    def left(self) -> float:
        return min(point[0] for point in self.box)

    @property
    def top(self) -> float:
        return min(point[1] for point in self.box)

    @property
    def center_y(self) -> float:
        return sum(point[1] for point in self.box) / len(self.box)


class OCRService:
    def __init__(self) -> None:
        self._engine: RapidOCR | None = None

    def _get_engine(self) -> RapidOCR:
        if self._engine is None:
            self._engine = RapidOCR()
        return self._engine

    def recognize(self, image: str | Path | bytes | np.ndarray) -> list[OCRToken]:
        results, _ = self._get_engine()(image)
        if not results:
            return []
        tokens: list[OCRToken] = []
        for result in results:
            if len(result) < 3:
                continue
            box, text, confidence = result[0], str(result[1]), float(result[2])
            normalized_box = tuple(tuple(float(value) for value in point) for point in box)
            tokens.append(OCRToken(text=text.strip(), box=normalized_box, confidence=confidence))
        return tokens
