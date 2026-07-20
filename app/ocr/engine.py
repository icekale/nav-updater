from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import cv2
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

    def recognize_tiled(
        self,
        image: str | Path | bytes | np.ndarray,
        *,
        tile_height: int = 2600,
        overlap: int = 128,
    ) -> list[OCRToken]:
        if tile_height <= 0 or overlap < 0 or overlap >= tile_height:
            raise ValueError("invalid OCR tile dimensions")
        source = _load_image(image)
        if source.ndim < 2 or source.shape[0] <= 0:
            raise ValueError("invalid OCR image dimensions")
        height = source.shape[0]
        if height <= tile_height:
            return self.recognize(source)

        tokens: list[OCRToken] = []
        offset_y = 0
        while True:
            end_y = min(offset_y + tile_height, height)
            recognized = self.recognize(source[offset_y:end_y])
            tokens.extend(_shift_token(token, offset_y) for token in recognized)
            if end_y == height:
                break
            offset_y += tile_height - overlap
        return _deduplicate_tokens(tokens)


def _load_image(image: str | Path | bytes | np.ndarray) -> np.ndarray:
    if isinstance(image, np.ndarray):
        return image
    if isinstance(image, bytes):
        loaded = cv2.imdecode(np.frombuffer(image, dtype=np.uint8), cv2.IMREAD_COLOR)
    else:
        loaded = cv2.imread(str(image), cv2.IMREAD_COLOR)
    if loaded is None:
        raise ValueError("unable to read OCR image")
    return loaded


def _shift_token(token: OCRToken, offset_y: int) -> OCRToken:
    return OCRToken(
        text=token.text,
        box=tuple((x, y + offset_y) for x, y in token.box),
        confidence=token.confidence,
    )


def _deduplicate_tokens(tokens: list[OCRToken]) -> list[OCRToken]:
    unique: list[OCRToken] = []
    for token in sorted(tokens, key=lambda item: (item.top, item.left, item.text)):
        duplicate = next(
            (
                index
                for index, existing in enumerate(unique)
                if existing.text == token.text
                and abs(existing.left - token.left) <= 2
                and abs(existing.top - token.top) <= 2
            ),
            None,
        )
        if duplicate is None:
            unique.append(token)
        elif token.confidence > unique[duplicate].confidence:
            unique[duplicate] = token
    return sorted(unique, key=lambda item: (item.top, item.left, item.text))
