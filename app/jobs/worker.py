from __future__ import annotations

import time
from collections.abc import Callable

from ..config import ensure_data_dir
from ..db import SessionLocal
from .processor import process_run
from .service import claim_next_run, fail_run

Processor = Callable[[int], None]


def run_once(processor: Processor) -> bool:
    session = SessionLocal()
    try:
        run = claim_next_run(session)
        if run is None:
            return False
        try:
            processor(run.id)
        except Exception as exc:  # pragma: no cover - exercised by worker process
            fail_run(session, run.id, str(exc))
        return True
    finally:
        session.close()


def main() -> None:
    ensure_data_dir()
    while True:
        run_once(_process_with_database)
        time.sleep(2)


def _process_with_database(run_id: int) -> None:
    session = SessionLocal()
    try:
        process_run(session, run_id)
    finally:
        session.close()


if __name__ == "__main__":
    main()
