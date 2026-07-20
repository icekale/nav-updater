# OCR Source Blank Classification Design

## Goal

When a performance-report cell explicitly uses an unavailable marker, clear the matching Excel cell without sending the product to manual review. Continue to review any field that OCR simply did not capture.

## Evidence And Decision

The 2026-07-10 China 50 report renders unavailable performance values as a dash-like marker. The current parser drops those tokens alongside genuinely absent OCR output, so both are treated as missing. OCR tokens alone cannot prove that a cell with no token is visually blank: it may contain a number that OCR missed.

This release therefore treats only recognized unavailable markers (`-`, `--`, em dash, and `N/A`) as a confirmed source blank. A source cell with no recognized token stays reviewable. This prevents a low-quality OCR read from silently erasing an existing Excel value.

## Data Flow

`extract_metric_rows()` records recognized unavailable markers in `OCRMetricRow.blank_metrics`, separately from numeric `metrics`. `process_run()` assigns those fields the `source_blank` status, excludes them from its missing-field review reason, and sends `None` for them to the Excel adapter.

The Excel adapter clears a cell only for an explicit `None` update that is not stale. A stale field with no value retains its existing cell value and red styling, preserving the established review behavior.

## Scope

- No database migration or new OCR provider.
- No automatic classification of token-free cells as blank.
- No change to product matching, public-fund calculation, or manual-review save semantics.

## Acceptance Criteria

1. A dash-like source token is preserved as a confirmed blank in the parsed row.
2. A matched row with only numeric values and confirmed blanks is `ready`, has no stale fields for its confirmed blanks, and sends `None` to Excel for them.
3. The output Excel clears a confirmed blank even when the template previously contained a value.
4. A field without an OCR token remains stale and reviewable.

