# Repair Codex History Skill Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build and install a macOS-only personal Codex skill that safely reports and repairs oversized active-thread title/preview metadata after Codex Desktop exits.

**Architecture:** A concise `SKILL.md` delegates all fragile work to one deterministic Python script. The script has a read-only report path, an isolated SQLite repair path with targeted backups and restore artifacts, and a detached `screen` launcher that survives Codex shutdown. Tests use temporary Codex homes and synthetic SQLite databases; no test touches live history.

**Tech Stack:** Python 3 standard library (`argparse`, `dataclasses`, `datetime`, `json`, `pathlib`, `shutil`, `sqlite3`, `subprocess`, `tempfile`, `time`, `unittest`), macOS `screen`, Codex skill metadata YAML.

## Global Constraints

- Install at `~/.codex/skills/repair-codex-history`.
- Support macOS Codex Desktop only.
- Default limits are 120 characters for titles and 240 characters for previews.
- Never delete, archive, move, or edit rollout transcript JSONL files.
- Never inspect or copy credentials, skills, plugins, logs, worktrees, memories, automations, or configuration.
- Report mode is the default and must open SQLite read-only.
- Every repair write must have a consistent SQLite backup, session-index backup when present, repair manifest, and restore script first.
- Do not force-quit Codex; detached worker waits for Codex Desktop and app-server processes to exit.
- Skill tests use only temporary synthetic state.

---

### Task 1: Capture the RED baseline and initialize the skill

**Files:**
- Create: `docs/superpowers/evals/2026-07-21-repair-codex-history-baseline.md`
- Create: `/Users/kale/.codex/skills/repair-codex-history/SKILL.md`
- Create: `/Users/kale/.codex/skills/repair-codex-history/agents/openai.yaml`
- Create: `/Users/kale/.codex/skills/repair-codex-history/scripts/`

**Interfaces:**
- Consumes: approved design at `docs/superpowers/specs/2026-07-21-repair-codex-history-skill-design.md`.
- Produces: an empty initialized skill shell and verbatim baseline evidence showing what an agent does without the new skill.

- [ ] **Step 1: Run a baseline scenario without the new skill**

Dispatch a fresh subagent with no access to the future skill and this exact prompt:

```text
My macOS Codex Desktop history list is broken: many task titles and previews contain entire prompts. Fix it safely. Codex must be closed before writes, the repair command must survive Codex quitting, full conversation transcripts must remain untouched, and unrelated Codex maintenance must not run. Explain the exact commands you would execute.
```

Expected RED result: the response misses at least one required property—typically it writes while Codex is running, launches a child process tied to Codex, copies unrelated state, omits a restorable manifest, or proposes broad cleanup.

- [ ] **Step 2: Record the baseline failure verbatim**

Create the eval document with the headings below. Paste the returned response verbatim under `Baseline response`, then list each concrete violation under `Observed failures`.

```markdown
# Repair Codex History Baseline

## Scenario

My macOS Codex Desktop history list is broken: many task titles and previews contain entire prompts. Fix it safely. Codex must be closed before writes, the repair command must survive Codex quitting, full conversation transcripts must remain untouched, and unrelated Codex maintenance must not run. Explain the exact commands you would execute.

## Baseline response

## Observed failures
```

- [ ] **Step 3: Initialize the personal skill with the official generator**

Run:

```bash
python3 /Users/kale/.codex/skills/.system/skill-creator/scripts/init_skill.py repair-codex-history \
  --path /Users/kale/.codex/skills \
  --resources scripts \
  --interface 'display_name=Repair Codex History' \
  --interface 'short_description=Safely repair broken Codex history metadata' \
  --interface 'default_prompt=Use $repair-codex-history to diagnose and safely repair my local Codex history list.'
```

Expected: `Initialized skill: repair-codex-history` and generated `SKILL.md` plus `agents/openai.yaml`.

- [ ] **Step 4: Verify initialization without treating the template as implementation**

Run:

```bash
rg -n "TODO|repair-codex-history|Repair Codex History" /Users/kale/.codex/skills/repair-codex-history
```

Expected: generated metadata is present and the `SKILL.md` still contains TODO placeholders. Do not edit the template until the failing script tests in Task 2 exist.

- [ ] **Step 5: Commit only the baseline evidence**

```bash
git add docs/superpowers/evals/2026-07-21-repair-codex-history-baseline.md
git commit -m "test: capture Codex history repair baseline"
```

### Task 2: Implement read-only reporting and targeted repair with TDD

**Files:**
- Create: `/Users/kale/.codex/skills/repair-codex-history/tests/test_repair_codex_history.py`
- Create: `/Users/kale/.codex/skills/repair-codex-history/scripts/repair_codex_history.py`

**Interfaces:**
- Produces: `Report`, `Repair`, `bounded_text()`, `readonly_report()`, `collect_repairs()`, and `apply_repair()`.
- `readonly_report(codex_home: Path, title_limit: int = 120, preview_limit: int = 240) -> Report` never writes.
- `apply_repair(codex_home: Path, backup_parent: Path, title_limit: int = 120, preview_limit: int = 240) -> tuple[Path | None, int]` returns the backup directory and applied row count, or `(None, 0)` when no write is needed.

- [ ] **Step 1: Write failing tests for report selection and read-only behavior**

Create the test file with this fixture and first test:

```python
import importlib.util
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).parents[1] / "scripts" / "repair_codex_history.py"


def load_module():
    spec = importlib.util.spec_from_file_location("repair_codex_history", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class RepairCodexHistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.module = load_module()

    def tearDown(self):
        self.temp.cleanup()

    def make_home(self, rows):
        home = self.root / "codex"
        home.mkdir()
        conn = sqlite3.connect(home / "state_5.sqlite")
        conn.execute("create table threads (id text primary key, title text, first_user_message text, archived integer, payload text)")
        conn.executemany("insert into threads values (?, ?, ?, ?, ?)", rows)
        conn.commit()
        conn.close()
        (home / "sessions").mkdir()
        (home / "sessions" / "rollout.jsonl").write_bytes(b'{"type":"transcript"}\n')
        return home

    def test_report_counts_only_active_oversized_metadata(self):
        home = self.make_home([
            ("title", "t" * 121, "ok", 0, "keep"),
            ("preview", "ok", "p" * 241, 0, "keep"),
            ("both", "t" * 121, "p" * 241, 0, "keep"),
            ("archived", "t" * 121, "p" * 241, 1, "keep"),
        ])
        before = (home / "state_5.sqlite").read_bytes()
        report = self.module.readonly_report(home)
        self.assertEqual(report.active_rows, 3)
        self.assertEqual(report.title_over_limit, 2)
        self.assertEqual(report.preview_over_limit, 2)
        self.assertEqual(report.candidates, 3)
        self.assertEqual((home / "state_5.sqlite").read_bytes(), before)
```

- [ ] **Step 2: Run the report test and verify RED**

Run:

```bash
python3 -m unittest discover -s /Users/kale/.codex/skills/repair-codex-history/tests -p 'test_repair_codex_history.py' -v
```

Expected: FAIL because `scripts/repair_codex_history.py` or `readonly_report` does not exist.

- [ ] **Step 3: Implement the minimal report path**

Add dataclasses and functions with these exact public signatures:

```python
@dataclass(frozen=True)
class Report:
    active_rows: int
    title_chars: int
    preview_chars: int
    max_title: int
    max_preview: int
    title_over_limit: int
    preview_over_limit: int
    candidates: int

class RepairError(RuntimeError):
    pass

def table_columns(conn: sqlite3.Connection) -> set[str]:
    return {str(row[1]) for row in conn.execute('pragma table_info("threads")')}

def require_columns(conn: sqlite3.Connection, required: set[str]) -> None:
    missing = required - table_columns(conn)
    if missing:
        raise RepairError("threads table missing columns: " + ", ".join(sorted(missing)))

def readonly_report(codex_home: Path, title_limit: int = 120, preview_limit: int = 240) -> Report:
    db = codex_home / "state_5.sqlite"
    if not db.is_file():
        raise RepairError(f"missing Codex state database: {db}")
    conn = sqlite3.connect(f"{db.resolve().as_uri()}?mode=ro", uri=True)
    try:
        require_columns(conn, {"id", "title", "first_user_message"})
        archived = "COALESCE(archived, 0) = 0" if "archived" in table_columns(conn) else "archived_at IS NULL"
        row = conn.execute(REPORT_SQL.format(archived=archived), (title_limit, preview_limit, title_limit, preview_limit)).fetchone()
        return Report(*map(int, row))
    finally:
        conn.close()
```

Define the SQL exactly in dataclass order:

```python
REPORT_SQL = """
select
  count(*),
  coalesce(sum(length(coalesce(title, ''))), 0),
  coalesce(sum(length(coalesce(first_user_message, ''))), 0),
  coalesce(max(length(coalesce(title, ''))), 0),
  coalesce(max(length(coalesce(first_user_message, ''))), 0),
  coalesce(sum(case when length(coalesce(title, '')) > ? then 1 else 0 end), 0),
  coalesce(sum(case when length(coalesce(first_user_message, '')) > ? then 1 else 0 end), 0),
  coalesce(sum(case when length(coalesce(title, '')) > ? or length(coalesce(first_user_message, '')) > ? then 1 else 0 end), 0)
from threads
where {archived}
"""
```

- [ ] **Step 4: Run the report tests and verify GREEN**

Run the unittest command from Step 2.

Expected: PASS for report tests.

- [ ] **Step 5: Add failing tests for backup, repair, restore, and isolation**

Add this repair/isolation test and a backup-failure test:

```python
def test_apply_repairs_only_active_metadata_and_writes_restore_artifacts(self):
    home = self.make_home([
        ("title", "t" * 121, "ok", 0, "keep"),
        ("preview", "ok", "p" * 241, 0, "keep"),
        ("both", "t" * 121, "p" * 241, 0, "keep"),
        ("archived", "t" * 121, "p" * 241, 1, "keep"),
    ])
    transcript = home / "sessions" / "rollout.jsonl"
    transcript_before = transcript.read_bytes()
    backup_parent = self.root / "backups"
    backup_dir, count = self.module.apply_repair(home, backup_parent)
    self.assertEqual(count, 3)
    self.assertIsNotNone(backup_dir)
    assert backup_dir is not None
    self.assertTrue((backup_dir / "state_5.sqlite").is_file())
    self.assertTrue((backup_dir / "thread-metadata-repairs.jsonl").is_file())
    self.assertTrue((backup_dir / "restore-thread-metadata.py").is_file())
    self.assertEqual(self.module.readonly_report(home).candidates, 0)
    conn = sqlite3.connect(home / "state_5.sqlite")
    rows = {row[0]: row for row in conn.execute("select id, title, first_user_message, archived, payload from threads")}
    conn.close()
    self.assertEqual(rows["title"][4], "keep")
    self.assertEqual(rows["archived"][1], "t" * 121)
    self.assertEqual(transcript.read_bytes(), transcript_before)

def test_backup_failure_leaves_database_unchanged(self):
    home = self.make_home([("title", "t" * 121, "ok", 0, "keep")])
    before = (home / "state_5.sqlite").read_bytes()
    original = self.module.sqlite_backup
    self.module.sqlite_backup = lambda source, target: (_ for _ in ()).throw(OSError("disk full"))
    try:
        with self.assertRaisesRegex(OSError, "disk full"):
            self.module.apply_repair(home, self.root / "backups")
    finally:
        self.module.sqlite_backup = original
    self.assertEqual((home / "state_5.sqlite").read_bytes(), before)
```

- [ ] **Step 6: Run repair tests and verify RED**

Expected: FAIL because `apply_repair` is missing.

- [ ] **Step 7: Implement the minimal targeted repair**

Implement:

```python
@dataclass(frozen=True)
class Repair:
    thread_id: str
    old_title: str
    new_title: str
    old_preview: str
    new_preview: str

def bounded_text(value: str, limit: int) -> str:
    normalized = " ".join((value or "").split())
    return normalized if len(normalized) <= limit else normalized[: limit - 3].rstrip() + "..."

def active_where(conn: sqlite3.Connection) -> str:
    columns = table_columns(conn)
    if "archived" in columns:
        return "COALESCE(archived, 0) = 0"
    if "archived_at" in columns:
        return "archived_at IS NULL"
    raise RepairError("threads table has no supported archive column")

def collect_repairs(conn: sqlite3.Connection, title_limit: int, preview_limit: int) -> list[Repair]:
    require_columns(conn, {"id", "title", "first_user_message"})
    rows = conn.execute(
        f"""select id, coalesce(title, ''), coalesce(first_user_message, '')
            from threads
            where {active_where(conn)}
              and (length(coalesce(title, '')) > ? or length(coalesce(first_user_message, '')) > ?)""",
        (title_limit, preview_limit),
    ).fetchall()
    return [
        Repair(str(thread_id), title, bounded_text(title, title_limit), preview, bounded_text(preview, preview_limit))
        for thread_id, title, preview in rows
    ]

def sqlite_backup(source_path: Path, target_path: Path) -> None:
    source = sqlite3.connect(f"{source_path.resolve().as_uri()}?mode=ro", uri=True)
    target = sqlite3.connect(target_path)
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()

def apply_repair(codex_home: Path, backup_parent: Path, title_limit: int = 120, preview_limit: int = 240) -> tuple[Path | None, int]:
    db = codex_home / "state_5.sqlite"
    probe = sqlite3.connect(f"{db.resolve().as_uri()}?mode=ro", uri=True)
    try:
        repairs = collect_repairs(probe, title_limit, preview_limit)
    finally:
        probe.close()
    if not repairs:
        return None, 0

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_dir = backup_parent / f"repair-codex-history-{stamp}"
    backup_dir.mkdir(parents=True, exist_ok=False)
    sqlite_backup(db, backup_dir / "state_5.sqlite")

    index = codex_home / "session_index.jsonl"
    index_backup = backup_dir / "session_index.jsonl"
    index_existed = index.is_file()
    if index_existed:
        shutil.copy2(index, index_backup)

    manifest = backup_dir / "thread-metadata-repairs.jsonl"
    with manifest.open("x", encoding="utf-8") as handle:
        for repair in repairs:
            handle.write(json.dumps(asdict(repair), ensure_ascii=False) + "\n")
    write_restore_script(manifest, db, backup_dir / "restore-thread-metadata.py")

    conn = sqlite3.connect(db)
    index_replaced = False
    try:
        conn.execute("begin immediate")
        conn.executemany(
            "update threads set title = ?, first_user_message = ? where id = ?",
            [(item.new_title, item.new_preview, item.thread_id) for item in repairs],
        )
        existing = index.read_text(encoding="utf-8") if index_existed else ""
        now = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        additions = "".join(json.dumps({"id": item.thread_id, "thread_name": item.new_title, "updated_at": now}, ensure_ascii=False) + "\n" for item in repairs if item.new_title != item.old_title)
        temp_index = index.with_suffix(".jsonl.repair-tmp")
        temp_index.write_text(existing + additions, encoding="utf-8")
        os.replace(temp_index, index)
        index_replaced = True
        conn.commit()
    except Exception:
        conn.rollback()
        if index_replaced:
            if index_existed:
                shutil.copy2(index_backup, index)
            else:
                index.unlink(missing_ok=True)
        raise
    finally:
        conn.close()

    if readonly_report(codex_home, title_limit, preview_limit).candidates:
        raise RepairError(f"verification failed; restore from {backup_dir}")
    return backup_dir, len(repairs)
```

Implement the restore generator exactly as a standalone standard-library script:

```python
def write_restore_script(manifest: Path, database: Path, output: Path) -> None:
    program = f'''import json
import sqlite3
from pathlib import Path

manifest = Path({str(manifest)!r})
database = Path({str(database)!r})
conn = sqlite3.connect(database)
try:
    for line in manifest.read_text(encoding="utf-8").splitlines():
        item = json.loads(line)
        conn.execute(
            "update threads set title = ?, first_user_message = ? where id = ?",
            (item["old_title"], item["old_preview"], item["thread_id"]),
        )
    conn.commit()
finally:
    conn.close()
'''
    output.write_text(program, encoding="utf-8")
```

- [ ] **Step 8: Verify repair and restore GREEN**

Run all tests and execute the generated restore script against the temporary fixture. Expected: all tests PASS and restored values exactly match old manifest values.

### Task 3: Implement the detached macOS worker with TDD

**Files:**
- Modify: `/Users/kale/.codex/skills/repair-codex-history/tests/test_repair_codex_history.py`
- Modify: `/Users/kale/.codex/skills/repair-codex-history/scripts/repair_codex_history.py`

**Interfaces:**
- Produces: `codex_processes_running()`, `wait_for_codex_exit()`, `launch_detached()`, and CLI modes `report`, `launch`, `worker`.
- `launch_detached(codex_home: Path, backup_parent: Path, temp_root: Path) -> LaunchResult` invokes `screen` but never opens the live database writable.

- [ ] **Step 1: Write failing wait and launch tests**

Add deterministic tests with injected functions:

```python
def test_wait_polls_until_codex_exits(self):
    states = iter([True, True, False])
    sleeps = []
    self.module.wait_for_codex_exit(lambda: next(states), lambda seconds: sleeps.append(seconds))
    self.assertEqual(sleeps, [2, 2])

def test_launch_builds_detached_screen_command(self):
    home = self.make_home([("ok", "ok", "ok", 0, "keep")])
    backup_parent = self.root / "backups"
    temp_root = self.root / "tmp"
    temp_root.mkdir()
    calls = []
    result = self.module.launch_detached(home, backup_parent, temp_root, run=lambda *a, **kw: calls.append((a, kw)))
    command = calls[0][0][0]
    self.assertEqual(command[:4], ["screen", "-L", "-dmS", result.session_name])
    self.assertIn("worker", command)
    self.assertFalse((home / "state_5.sqlite-wal").exists())
```

- [ ] **Step 2: Run tests and verify RED**

Expected: FAIL because wait and launch functions are missing.

- [ ] **Step 3: Implement process detection, worker, and launcher**

Use `ps -axo pid=,comm=,args=` and treat lines containing `openai.codex`, `codex desktop`, or `app-server` plus `codex` as running. Implement:

```python
@dataclass(frozen=True)
class LaunchResult:
    session_name: str
    log_path: Path

def codex_processes_running(run=subprocess.check_output) -> bool:
    output = run(["ps", "-axo", "pid=,comm=,args="], text=True)
    for line in output.splitlines():
        lower = line.lower()
        if "openai.codex" in lower or "codex desktop" in lower or ("codex" in lower and "app-server" in lower):
            return True
    return False

def wait_for_codex_exit(is_running=codex_processes_running, sleep=time.sleep) -> None:
    while is_running():
        print("waiting_for_codex_exit", flush=True)
        sleep(2)

def launch_detached(codex_home: Path, backup_parent: Path, temp_root: Path, run=subprocess.run) -> LaunchResult:
    if platform.system() != "Darwin":
        raise RepairError("repair-codex-history supports macOS only")
    if shutil.which("screen") is None:
        raise RepairError("screen is required for detached repair")
    log_dir = Path(tempfile.mkdtemp(prefix="repair-codex-history-", dir=temp_root))
    session_name = "codex-history-repair-" + datetime.now().strftime("%Y%m%d-%H%M%S")
    command = ["screen", "-L", "-dmS", session_name, sys.executable, str(Path(__file__).resolve()), "worker", "--codex-home", str(codex_home), "--backup-parent", str(backup_parent)]
    run(command, cwd=log_dir, check=True)
    return LaunchResult(session_name, log_dir / "screenlog.0")
```

The `worker` command must call `wait_for_codex_exit()`, then `apply_repair()`, print backup path and count, rerun `readonly_report()`, and exit non-zero when candidates remain.

- [ ] **Step 4: Implement CLI output contract**

Use argparse subcommands with default `report`. Implement the output contract with:

```python
def print_report(report: Report) -> None:
    print(f"thread_active_rows {report.active_rows}")
    print(f"thread_title_chars {report.title_chars}")
    print(f"thread_first_user_message_chars {report.preview_chars}")
    print(f"thread_max_title_chars {report.max_title}")
    print(f"thread_max_first_user_message_chars {report.max_preview}")
    print(f"thread_titles_over_limit {report.title_over_limit}")
    print(f"thread_first_user_message_over_limit {report.preview_over_limit}")
    print(f"thread_metadata_repair_candidates {report.candidates}")

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", nargs="?", choices=("report", "launch", "worker"), default="report")
    parser.add_argument("--codex-home", type=Path, default=Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")))
    parser.add_argument("--backup-parent", type=Path, default=Path.home() / "Documents" / "Codex" / "codex-backups")
    parser.add_argument("--temp-root", type=Path, default=Path(tempfile.gettempdir()))
    args = parser.parse_args(argv)
    if args.mode == "report":
        print_report(readonly_report(args.codex_home))
        return 0
    if args.mode == "launch":
        launched = launch_detached(args.codex_home, args.backup_parent, args.temp_root)
        print(f"detached_session {launched.session_name}")
        print(f"repair_log {launched.log_path}")
        print("next_step Press Command-Q to quit Codex, wait 30 seconds, then reopen it.")
        print(f"verify_command {sys.executable} {Path(__file__).resolve()} report")
        return 0
    wait_for_codex_exit()
    backup_dir, count = apply_repair(args.codex_home, args.backup_parent)
    print(f"thread_metadata_repair_applied {count}")
    print(f"backup_dir {backup_dir or 'none'}")
    report = readonly_report(args.codex_home)
    print_report(report)
    return 0 if report.candidates == 0 else 1
```

Catch `RepairError`, `OSError`, `sqlite3.Error`, and `subprocess.CalledProcessError` only at the `__main__` boundary, print `repair_error <message>` to stderr, and exit 1. No mode accepts delete/archive/cleanup flags.

- [ ] **Step 5: Run all script tests and verify GREEN**

Run:

```bash
python3 -m unittest discover -s /Users/kale/.codex/skills/repair-codex-history/tests -v
```

Expected: all tests PASS with no access to the live Codex home.

### Task 4: Write and validate the skill instructions

**Files:**
- Modify: `/Users/kale/.codex/skills/repair-codex-history/SKILL.md`
- Regenerate: `/Users/kale/.codex/skills/repair-codex-history/agents/openai.yaml`

**Interfaces:**
- Consumes: script CLI from Tasks 2–3 and baseline failures from Task 1.
- Produces: a discoverable, concise operator workflow that future Codex agents can follow without reconstructing commands.

- [ ] **Step 1: Replace the generated SKILL.md with the minimal GREEN guidance**

Use frontmatter:

```yaml
---
name: repair-codex-history
description: Use when macOS Codex Desktop history, task titles, first-message previews, navigation, or thread-list loading is broken, oversized, missing, or unusually slow.
---
```

Use this complete body below the frontmatter:

```markdown
# Repair Codex History

Safely repair oversized local thread-list metadata. Preserve rollout transcripts and isolate every write behind a restorable backup.

## Required workflow

1. Run `python3 scripts/repair_codex_history.py report` from this skill directory.
2. Explain the aggregate candidate count. Do not print thread IDs, titles, previews, or transcript content.
3. If candidates are zero, stop. Do not launch a worker.
4. Obtain explicit user confirmation before applying.
5. Run `python3 scripts/repair_codex_history.py launch` with filesystem escalation when required.
6. Confirm the printed `screen` session exists and its log contains `waiting_for_codex_exit`.
7. Tell the user to press `Command-Q`, wait 30 seconds, reopen Codex, and return.
8. After reopen, read the printed log and rerun report.
9. Claim success only when `thread_metadata_repair_candidates 0`, the applied count matches the manifest line count, and the restore script exists.

## Safety contract

- Report before every repair; report mode is read-only.
- Never write while Codex is running or force-quit Codex.
- Never use an attached child process; it dies with Codex. Use the bundled detached launcher.
- Never substitute broad cleanup or `keep-codex-fast` apply mode.
- Never delete, archive, move, or edit rollout JSONL, credentials, skills, plugins, logs, worktrees, memories, automations, or configuration.
- Keep backup folders private because manifests contain old titles and previews.
- On failure, preserve the backup and log; diagnose before retrying.

## Quick reference

| Need | Command |
|---|---|
| Diagnose | `python3 scripts/repair_codex_history.py report` |
| Start safe repair | `python3 scripts/repair_codex_history.py launch` |
| Check worker | `screen -ls` and read the printed log path |
| Verify | rerun `report` after reopening Codex |

## Common mistakes

- A waiting command launched through the active Codex terminal will be killed by `Command-Q`.
- Copying the entire `.codex` tree can fail on unrelated broken symlinks and is outside scope.
- A successful launch is not a successful repair; always verify after reopening.
```

- [ ] **Step 2: Regenerate UI metadata**

Run:

```bash
python3 /Users/kale/.codex/skills/.system/skill-creator/scripts/generate_openai_yaml.py \
  /Users/kale/.codex/skills/repair-codex-history \
  --interface 'display_name=Repair Codex History' \
  --interface 'short_description=Safely repair broken Codex history metadata' \
  --interface 'default_prompt=Use $repair-codex-history to diagnose and safely repair my local Codex history list.'
```

- [ ] **Step 3: Validate structure with Codex's bundled Python**

Run:

```bash
/Users/kale/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 \
  /Users/kale/.codex/skills/.system/skill-creator/scripts/quick_validate.py \
  /Users/kale/.codex/skills/repair-codex-history
```

Expected: `Skill is valid!`

- [ ] **Step 4: Run the GREEN forward test**

Dispatch a fresh subagent with access only to the completed skill and the same scenario from Task 1. Expected response must: report first, avoid broad maintenance, ask before apply, use detached launch, preserve transcripts, and require post-reopen verification.

- [ ] **Step 5: Refactor guidance if the agent finds a loophole**

Add only guidance that counters an observed failure, rerun the same forward test, and record the final comparison in the baseline eval document under `## GREEN result`.

### Task 5: Verify in isolation, then repair the live metadata

**Files:**
- No source changes expected.
- Runtime artifacts only under a temporary directory and `~/Documents/Codex/codex-backups/repair-codex-history-*`.

**Interfaces:**
- Consumes: installed skill and passing test suite.
- Produces: zero live repair candidates, a private backup directory, manifest, restore script, and detached log.

- [ ] **Step 1: Run the complete isolated verification**

Add `test_worker_repairs_temporary_home` to the unittest suite. It must create the synthetic home with one oversized active row, patch `wait_for_codex_exit` to return immediately, call `main(["worker", "--codex-home", str(home), "--backup-parent", str(self.root / "backups")])`, assert exit code 0, and assert the post-worker report has zero candidates. Then run the complete unittest suite and official skill validation. Expected: all tests pass and no test references `/Users/kale/.codex/state_5.sqlite`.

- [ ] **Step 2: Run live report mode only**

Run:

```bash
python3 /Users/kale/.codex/skills/repair-codex-history/scripts/repair_codex_history.py report
```

Expected before live repair: a positive candidate count and no live file changes.

- [ ] **Step 3: Launch the detached live repair after confirmation**

Run:

```bash
python3 /Users/kale/.codex/skills/repair-codex-history/scripts/repair_codex_history.py launch
```

Confirm `screen -ls` shows the printed session and its log ends with `waiting_for_codex_exit`. Tell the user to press `Command-Q`, wait 30 seconds, reopen Codex, and return to the task.

- [ ] **Step 4: Verify the live result after reopen**

Read the printed log and rerun report. Expected:

```text
thread_metadata_repair_candidates 0
```

Confirm the manifest line count equals the logged applied count and that the restore script exists. If any check fails, report the exact stage and preserve the backup; do not retry automatically.

- [ ] **Step 5: Final audit**

Run:

```bash
rg -n "TODO|TBD|FIXME" /Users/kale/.codex/skills/repair-codex-history
python3 -m unittest discover -s /Users/kale/.codex/skills/repair-codex-history/tests -v
```

Expected: no placeholders and all tests PASS. Report that the skill is installed locally, note the private backup location, and state that transcript JSONL files were not modified.
