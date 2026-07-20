# PaddleOCR-VL Provider Design

## Goal

Allow an operator to opt into PaddleOCR-VL for private-report screenshots while preserving the existing local RapidOCR default, conservative product matching, review workflow, and Excel output contract.

## Confirmed Evidence

On the 2026-07-10 3840×16280 report, PaddleOCR-VL completed in 28 seconds and returned five HTML tables with 177 data rows. Its output matched the 12 values of the 仁桥、开思、静瑞 rows already obtained by local OCR; it also represented 静瑞 2021 as a visible `-` marker. Local OCR parsed 250 rows and included a spurious one-metric row.

Paddle also flattened report footnotes into product names such as `仁桥金选泽源5B1`, failed to identify 浑瑾岳桐, and confused 勤辰 with another product. It must therefore never loosen automatic product matching in this release.

## Configuration And Failure Behavior

`OCR_BACKEND=rapid` remains the default. `OCR_BACKEND=paddle` requires a non-empty `PADDLE_OCR_TOKEN`; no token is committed in source, examples, logs, benchmark output, or audit context.

The Paddle adapter submits an image job, polls until done/failed or a configured timeout, downloads the short-lived result URL, and raises a clear OCR error on HTTP, malformed response, failed job, or timeout. Paddle failures do not fall back to RapidOCR in the same run: the batch fails visibly and keeps the existing source workbook unchanged. This avoids silently mixing external and local extraction results.

## Adapter Boundary

`PaddleOCRService.recognize_tiled(path)` returns the existing `list[OCRToken]` interface. It reads each HTML table, ignores rows without a product-name cell, and synthesizes deterministic cell coordinates per table, row, and column. The existing `extract_metric_rows()` parser therefore continues to recognize headers, percentages, source blanks, and repeated tables without a second processing pipeline.

The adapter keeps the captured Paddle product-name text exactly. It does not strip terminal digits or infer product aliases. Existing exact, catalog, and unique leading-Chinese-prefix matching decide whether a row is safe; unresolved names go to manual review.

## Scope

- No frontend selector, database migration, API result persistence, automatic footnote removal, or local fallback.
- No token in Git, Docker image, or benchmark labels.
- Screenshots are uploaded to the Paddle external service only when the operator explicitly configures `OCR_BACKEND=paddle` and supplies a token.

## Acceptance Criteria

1. With default configuration, the processor instantiates the existing local OCR service.
2. With Paddle selected and a token, a mocked job response containing a table yields parser-compatible tokens, including a `-` blank marker.
3. Missing token, failed job, malformed result, and timeout raise a clear error without calling RapidOCR.
4. A Paddle table row lacking a product name creates no product row.
5. The full test suite remains green, and the README documents privacy, configuration, and disable/rollback steps.
