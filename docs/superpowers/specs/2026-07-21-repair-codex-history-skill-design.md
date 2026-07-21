# Repair Codex History Skill Design

## Goal

Create a personal Codex skill named `repair-codex-history` for macOS Codex Desktop. It must safely diagnose and repair oversized active-thread display metadata without modifying conversation transcripts or performing unrelated maintenance.

## Trigger and Scope

The skill should trigger when users report that Codex historical conversations, task lists, titles, previews, navigation, or history loading are broken, missing, unusually slow, or filled with oversized prompt text.

The skill supports the local macOS Codex Desktop state under `${CODEX_HOME:-~/.codex}`. It does not support Windows or Linux, archive conversations, move worktrees, rotate logs, prune configuration, repair transcript JSONL, or terminate Codex automatically.

## Structure

Install the skill at `~/.codex/skills/repair-codex-history` with only the files needed at runtime:

- `SKILL.md`: trigger conditions, safety contract, and operator workflow.
- `agents/openai.yaml`: display name, short description, and default prompt.
- `scripts/repair_codex_history.py`: deterministic report, detached-worker, repair, restore, and verification logic.
- `tests/test_repair_codex_history.py`: isolated tests using temporary SQLite databases and fake process state.

No README, assets, or broad maintenance helpers are needed.

## Workflow

### Report mode

Report mode is the default and must be read-only. It opens `state_5.sqlite` in read-only mode and reports pseudonymous aggregate values:

- active thread count;
- total and maximum title and preview lengths;
- titles longer than 120 characters;
- previews longer than 240 characters;
- total repair candidates.

Raw titles, previews, thread IDs, and transcript content are excluded unless an explicit details mode is added later. Details mode is not part of the initial implementation.

### Detached repair mode

The launch command creates a detached `screen` session and returns immediately. The worker writes a log into a unique directory under the system temporary directory, waits until Codex Desktop and its app server have exited, and then performs the repair.

The parent command must print:

- the detached session name;
- the log path;
- a clear instruction to quit Codex with `Command-Q`, wait, and reopen it;
- the verification command to run after reopening.

If `screen` is unavailable or a session with the same name is active, fail without changing local state and provide a concrete error.

### Backup and repair

Before any database write, create a timestamped private backup under `~/Documents/Codex/codex-backups/repair-codex-history-*` containing:

- a consistent SQLite backup of `state_5.sqlite`;
- a copy of `session_index.jsonl` when present;
- `thread-metadata-repairs.jsonl`, recording old and new values for each repaired row;
- `restore-thread-metadata.py`, which restores the recorded SQLite values.

The repair transaction selects non-archived threads whose title exceeds 120 characters or whose `first_user_message` exceeds 240 characters. Normalize whitespace, truncate at the configured limit, and append `...` when truncation occurs. Update only `threads.title` and `threads.first_user_message`. Append repaired names to `session_index.jsonl` using the currently supported name-update record format so reconciliation does not restore oversized fallback names.

Do not inspect, copy, edit, move, or delete session rollout JSONL files, credentials, skills, plugins, logs, worktrees, memories, automations, or configuration.

## Failure Handling

- Missing database or required columns: stop with a specific diagnostic and make no changes.
- Codex still running in direct worker mode: wait; never force-quit it.
- Backup failure: stop before opening the writable transaction.
- Manifest or restore-script failure: stop before updating SQLite.
- SQLite update failure: roll back the transaction and preserve the backup.
- Session-index update failure: restore the old SQLite values from the in-memory repair manifest before exiting, then report that no repair remains committed.
- Verification mismatch: report failure with the backup and log paths; do not retry automatically.

All errors must produce a non-zero exit status. The detached log must contain enough information to distinguish waiting, backup, repair, and verification failures without printing conversation content.

## Verification

Automated tests use temporary directories and synthetic SQLite data. They must cover:

1. report mode detects oversized title-only, preview-only, and combined candidates without writes;
2. archived threads are ignored;
3. repair truncates only the intended columns and preserves unrelated columns;
4. full SQLite backup, repair manifest, and restore script are produced before writes;
5. restoring returns old title and preview values;
6. backup failure prevents all database updates;
7. direct worker waits while a fake Codex process is present;
8. detached launch builds the expected `screen` command without touching the live Codex database;
9. post-repair report returns zero candidates for the repaired fixture;
10. transcript/session files outside the targeted metadata files remain unchanged.

The live verification sequence is:

1. run read-only report;
2. launch detached repair;
3. quit Codex and wait for the worker to exit;
4. reopen Codex;
5. rerun report and require zero candidates;
6. confirm the backup manifest line count equals the applied repair count.

## Success Criteria

- The skill auto-discovers from `~/.codex/skills/repair-codex-history`.
- Report mode is demonstrably read-only.
- Repair survives Codex Desktop shutdown because it runs in an independent `screen` session.
- A broken symlink elsewhere under `~/.codex/skills` cannot affect backup or repair.
- No conversation transcript is shortened, deleted, archived, or moved.
- Every write is preceded by a restorable backup and manifest.
- Unit tests, skill validation, and a temporary-database end-to-end test pass.
