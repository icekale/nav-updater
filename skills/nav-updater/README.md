# Public NAV Updater Skill

This directory contains the public, data-free agent skill for the `nav-updater`
repository. It describes how to operate the local NAV workbook workflow without
publishing customer data, source screenshots, credentials, or generated files.

## Install

Copy or symlink `skills/nav-updater/` into the skills directory used by your agent:

```bash
cp -R skills/nav-updater ~/.codex/skills/nav-updater
```

The exact destination can differ by agent. Keep the repository's source code and
local operating data separate from the installed copy.

## Scope

The skill covers:

- cutoff-date batch discipline;
- exact product matching and manual review;
- spreadsheet and screenshot upload workflow;
- weekly, MTD, YTD, annual, Sharpe, and drawdown rules;
- benchmark alignment and simple excess-return calculation;
- OCR regression evidence and privacy-safe operating boundaries.

The repository intentionally does not include real product catalogs, NAV history,
source screenshots, credentials, uploaded workbooks, or generated result files.

## Verify

From the repository root:

```bash
.venv/bin/pytest tests/test_skill_package.py -q
.venv/bin/pytest -q
git diff --check
```
