from __future__ import annotations

from ..config import ensure_data_dir
from ..db import SessionLocal
from ..ocr.regression import claim_next_regression, run_regression


def run_once() -> bool:
    session = SessionLocal()
    try:
        run = claim_next_regression(session)
        if run is None:
            return False
        run_regression(
            session,
            run.id,
            samples_root=ensure_data_dir() / "ocr-quality" / "samples",
        )
        return True
    finally:
        session.close()
