from __future__ import annotations

from ..config import ensure_data_dir
from ..db import SessionLocal
from ..ocr.regression import claim_next_regression, run_regression
from ..time import china_now


def run_once() -> bool:
    session = SessionLocal()
    try:
        run = claim_next_regression(session)
        if run is None:
            return False
        try:
            run_regression(
                session,
                run.id,
                samples_root=ensure_data_dir() / "ocr-quality" / "samples",
            )
        except Exception as exc:  # pragma: no cover - exercised by worker process
            run.status = "failed"
            run.error_message = str(exc)
            run.finished_at = china_now()
            session.commit()
        return True
    finally:
        session.close()
