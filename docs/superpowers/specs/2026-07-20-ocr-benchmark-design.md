# OCR Benchmark Design

## Goal

Provide a repeatable, local benchmark that measures private-fund report OCR against researcher-confirmed product names and 12 performance fields. It must report product matching, field accuracy, missing recognition, and wrong-column errors before any OCR rule change is accepted.

## Dataset

The first dataset contains 30 product rows: three rows from each of the ten supplied weekly reports. It deliberately includes complete rows, rows containing report unavailable markers, and names that previously needed conservative truncated-name matching.

Images are not committed. Each labelled case references a filename relative to an operator-supplied image root and records the SHA-256 of the source image. The label file stores a normalized product name and each expected metric as either a decimal fraction string or `null` for a visually confirmed unavailable value.

## Evaluation

The benchmark runs the deployed OCR parser semantics: `OCRService.recognize_tiled()` followed by `extract_metric_rows()`.

- A product is matched only when the normalized OCR name equals the confirmed product name.
- A numeric field is correct only when its decimal result exactly equals the labelled decimal result.
- An expected `null` is correct only when the parser reports that metric in `blank_metrics`; an absent token is a miss, not a confirmed blank.
- A wrong-column error is recorded when the expected value is parsed under a different uniquely-valued metric column in the same product row.

The command produces a JSON details file and a Markdown summary. The summary gives a total and per-image product match rate, field accuracy, missed-field rate, wrong-column rate, and the individual mismatches required for rule improvement.

## Workflow

1. A researcher confirms 30 source rows in the label JSON using the screenshots, marking report dashes as `null`.
2. The benchmark validates source hashes, runs each source image once, and writes an immutable timestamped result outside the repository.
3. Any OCR parser or engine change must run the same labels and retain or improve the four metrics; new labels are added only as a separate reviewed change.

## Scope

- No image upload, external OCR provider, database migration, dashboard, or automatic correction.
- No use of public-fund data or saved manual-review values.
- The initial label manifest is intentionally a checked-in example; confirmed researcher labels are local data, not fabricated from OCR output.

## Acceptance Criteria

1. The evaluator rejects a missing source image or SHA-256 mismatch.
2. A test fixture with a numeric value and a confirmed blank reports both fields as correct.
3. A test fixture with a shifted value reports a wrong-column error.
4. One command writes JSON and Markdown results without modifying any database, run history, or input image.
