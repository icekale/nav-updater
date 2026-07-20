from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate OCR against confirmed report labels.")
    parser.add_argument("--labels", type=Path, required=True, help="Version 1 benchmark label JSON")
    parser.add_argument(
        "--images-root", type=Path, required=True, help="Directory containing label images"
    )
    parser.add_argument(
        "--output-dir", type=Path, required=True, help="Empty directory for benchmark output"
    )
    return parser.parse_args()


def main() -> int:
    from app.ocr.benchmark import (
        BenchmarkFormatError,
        BenchmarkSourceError,
        load_cases,
        run_benchmark,
        verify_source_hashes,
        write_report,
    )
    from app.ocr.engine import OCRService

    args = parse_args()
    try:
        cases = load_cases(args.labels)
        sources = verify_source_hashes(cases, args.images_root)
        report = run_benchmark(cases, sources, OCRService().recognize_tiled)
        write_report(report, args.output_dir)
    except (BenchmarkFormatError, BenchmarkSourceError) as exc:
        raise SystemExit(f"OCR benchmark failed: {exc}") from exc
    print(f"OCR benchmark completed: {args.output_dir / 'summary.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
