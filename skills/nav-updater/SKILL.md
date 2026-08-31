---
name: nav-updater
description: Use when updating investment research NAV workbooks from spreadsheets, screenshots, or public fund data in this repository.
---

# NAV Updater Skill

Use this skill for recurring NAV workbook updates, screenshot-based value extraction,
public fund lookup, review of uncertain rows, and calculation of return and risk
metrics. This is a generic operating guide; product names, identifiers, screenshots,
workbooks, credentials, and generated reports stay outside the public repository.

## When to Use

- A new cutoff-date NAV workbook needs to be processed.
- A screenshot contains private, channel, or public fund values that must be matched
  to a product catalog.
- A result workbook needs weekly, month-to-date, year-to-date, annual, Sharpe, or
  maximum-drawdown metrics.
- An OCR result needs human review or a verified regression sample.
- A meeting-tracking workbook needs to be imported alongside an NAV update.

Do not use this skill to invent a product match, silently overwrite an original
workbook, store customer data in Git, or bypass an upstream platform login.

## Procedure

1. Confirm the real cutoff date and keep all source files for one cutoff in the same
   batch. Do not mix screenshots from different reporting dates.
2. Prepare the product catalog with exact `product_name`, `product_code`, and
   `product_type` fields. Prefer exact product-code or exact-name matches; route
   ambiguous names and duplicate candidates to review.
3. Start the local stack from the repository root:

   ```bash
   cp .env.example .env
   # Set a strong local SESSION_SECRET and administrator credentials in .env.
   docker compose up -d --build
   docker compose ps
   ```

4. Upload the original workbook and the clearest available PNG/JPEG screenshots in
   the web UI. Keep the original workbook immutable; the result is a new file.
5. Review every `needs_review`, `partial`, `stale`, or `failed` row. Record the
   evidence and only fill values that are visible in the source. An unfilled field
   must remain unchanged and visibly flagged in the result.
6. Use the repository's metric rules: weekly return uses the cutoff-week window,
   MTD uses the prior month-end, YTD uses the prior year-end, and annual returns
   use completed calendar years. For two NAV observations, return is
   `end_value / start_value - 1`.
7. For benchmark comparison, align the benchmark observation to the same disclosure
   date or the latest valid observation on or before it. Calculate simple excess as
   `fund_return - benchmark_return`; never use a single-day move when the report
   interval spans a week or month.
8. Generate the result only after review, then download it and verify the output
   filename, cutoff date, product identity, and unchanged original file.
9. For OCR improvements, promote only human-confirmed screenshots to regression
   samples. Run the benchmark before changing a production OCR rule.
10. Keep private source files, local `.env`, uploads, generated workbooks, and OCR
    samples in a controlled directory excluded by `.gitignore`.

## Privacy

- The public repository contains reusable code and documentation only.
- Never commit real fund names, product identifiers, NAV history, screenshots,
  customer information, mailbox addresses, access tokens, or generated reports.
- Store secrets in the local environment or a secret manager, never in source,
  fixtures, filenames, logs, or review notes.
- Do not send screenshots or workbook contents to an external OCR provider unless
  the data owner has explicitly approved that transfer.
- Preserve source workbooks and evidence locally so a result can be audited without
  publishing the underlying data.

## Verification

Run the focused tests after a change:

```bash
.venv/bin/pytest tests/test_skill_package.py -q
```

Run the full suite before publishing code:

```bash
.venv/bin/pytest -q
```

Also verify:

- `git diff --check` is clean.
- The working tree contains no `.env`, uploads, screenshots, generated workbooks,
  private catalogs, or OCR samples.
- The result workbook has the requested cutoff date and expected row count.
- Any uncertain row is explicitly flagged rather than silently guessed.
- The original workbook remains byte-for-byte unchanged.
