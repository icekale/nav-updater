# OCR Review Productization Design

## Goal

Improve the reliability and usability of the private-fund performance-report workflow without changing the existing deployment, Excel contract, or historical records.

## Confirmed Evidence

The supplied China 50 reports contain repeated table blocks in one 3840-pixel-wide long image. The 2026-07-10 acceptance run completed in 42 seconds. Its three review rows had genuinely blank source cells. The parser weakness is that it finds only the first table header and reuses that column layout for later blocks.

## OCR Design

`extract_metric_rows` will discover every valid table header. Each header defines a block ending immediately before the next header, and each block derives its own product-name, code, and metric positions. Existing YTD and split risk-header recovery stays scoped to the header block.

Product matching remains conservative: exact code/name, historical name, and unique leading-Chinese-prefix matching are the only automatic routes. Low-confidence or absent source fields remain reviewable rather than guessed.

## Workflow Design

The upload screen will state that one batch may contain only screenshots from one report date. The preview will use human-readable labels, status summaries, recognized-field counts, and direct review actions. A review card will separate missing fields from already recognized fields while retaining existing product selection, draft preservation, validation, and partial-update behavior.

## Scope Boundaries

This release does not add a general dashboard, a database migration, fuzzy matching, a new external OCR service, or new data sources.

## Acceptance Criteria

1. A shifted later table uses its own header geometry.
2. Existing YTD and split risk-header tests stay green.
3. Preview exposes Chinese status labels, counts, recognized-field counts, and a review action.
4. Review separates missing and recognized fields without changing save semantics.
5. The full test suite and Docker deployment contract remain valid.
