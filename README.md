```
  ___ _ __
 / __| '_ \
 \__ \ |_) |
 |___/ .__/
     |_|
  CCExtractor CI · AI-friendly CLI
  drive CI investigations from the terminal — no UI, no HTML scraping
```

# sp — CCExtractor Sample Platform CLI

`sp` is a command-line client for the [CCExtractor Sample Platform](https://github.com/CCExtractor/sample-platform)
REST API. It lets a developer **or an AI agent** investigate CI runs end-to-end
from the terminal — no web frontend required.

Output defaults to **JSON** (ideal for agents and scripts), with a human-friendly
`-o table` view. Running `sp` with no arguments prints the banner above along
with a map of the command groups.

Driving it from an agent? Read [AGENTS.md](AGENTS.md) instead — it covers the
same ground with the safety rules and JSON shapes an agent needs.

## Quick start

```bash
pip install -e .                                                   # installs the `sp` command
export SP_BASE_URL=https://sampleplatform.ccextractor.org/api/v1   # or your own instance
sp auth login --email you@example.com --scope runs:read --scope results:read
sp -o table investigate 9412                                       # first real command
```

That last line prints the run header, the pass/fail counts, and every failure
labelled with why it failed.

## Install

```bash
pip install -e .                                     # from a clone
pip install git+https://github.com/CCExtractor/sp_cli  # straight from GitHub
```

Python 3.10 or newer. Both forms install the `sp` command onto your `PATH`.

## Configure

`sp` needs to know where the API lives and (optionally) a bearer token:

```bash
export SP_BASE_URL=https://sampleplatform.ccextractor.org/api/v1   # or your instance
export SP_API_TOKEN=<your-token>                                   # if the API requires auth
```

Both can also be passed per-command with `--base-url` and `--token`.

Or log in once and let `sp` remember the token:

```bash
sp auth login --email you@example.com
```

That writes the token to `~/.config/sp/config.json` with mode `0600`. Precedence
is `--token` > `SP_API_TOKEN` > the saved file, so an explicit credential always
wins. `sp auth logout` revokes it and clears the file; `--no-save` skips writing
it at all.

**Ask only for the scopes you need.** A token created with `--scope runs:read
--scope results:read --scope system:read` can read everything the investigation
commands touch and cannot change anything — the right default for exploring a
live deployment, and essential if an agent is driving. The seven scopes are
`runs:read`, `runs:write`, `results:read`, `baselines:write`, `system:read`,
`system:write`, and `tokens:manage`; a token lasts at most 30 days.

Other global options: `-o/--output {json,table}`, `--timeout N` (seconds, per
request), `--retries N`, `--no-color`, and `--version`.

## Usage

### Investigating a failure

```bash
sp investigate <run_id>              # one-shot triage: info + counts + classified failures
sp investigate <run_id> --with-history   # ... and whether each failure is new
sp run summary <run_id>              # pass/fail summary for a run
sp run failures <run_id>             # failing tests, each auto-classified
sp run error-summary <run_id>        # grouped error counts — cheapest first look
sp run errors <run_id>               # structured per-test errors
sp run infra-errors <run_id>         # VM / checkout / build / worker failures
sp run diff <run_id> <id>            # expected-vs-actual diff for a result
sp run logs <run_id> --level error   # build log, cursor-paginated
sp run artifacts <run_id>            # binary, coredump, outputs, build log
```

To get the actual output file rather than the JSON envelope it arrives in:

```bash
sp run output <run_id> <id> --decode > actual.srt
sp run output <run_id> <id> --side expected --decode > expected.srt
diff expected.srt actual.srt
```

### Running and browsing

```bash
sp health                            # API + dependency health
sp queue                             # queue depth and running jobs
sp run ls                            # list CI runs
sp run ls --pr 2309                  # ... just one pull request's runs
sp run create --commit <sha> --platform linux --repository owner/repo
sp sample ls / show / details <id>   # media samples
sp regression ls / show <id>         # regression-test definitions
sp category ls                       # categories, with test counts
```

### Maintaining tests (contributor or admin)

```bash
sp regression create --sample-id 42 --command '-autoprogram' --category DVB
sp regression edit 18 --inactive     # retire a test that already has history
sp regression rm 18                  # only allowed if it has never run
sp category create DVB --description 'DVB subtitles'
```

### Administration (admin only)

```bash
sp auth whoami                       # who this token is, and its role
sp auth users                        # list platform users
sp admin maintenance                 # is CI paused?
sp admin pause linux                 # stop dispatching to a platform
sp admin blocked-users add <github_user_id> --comment 'spam'
sp admin forbidden-extensions add exe
```

Add `-o table` to any command for a human-readable view (default is JSON):

```bash
sp -o table investigate 9299
```

In table mode on a terminal, the `code` and `verdict` columns are colorized.
Colour is dropped automatically when the output is piped, and can be turned off
with `--no-color` or the standard `NO_COLOR` environment variable — JSON output
is never colorized.

### The classifier

`sp` labels each failure with a stable code — `SEGFAULT`, `ABORT`, `TIMEOUT`,
`EXIT_CODE_MISMATCH`, `MISSING_OUTPUT`, `OUTPUT_DIFF`, `PASS` — so a person or an
agent gets a straight answer about *why* a test failed, without reading logs.

One row is reported per failing sample, which is not the same unit `sp run
summary` counts. Its `error_count` counts individual errors, and a single sample
can raise more than one: a test that crashes *and* writes a wrong output is one
`SEGFAULT` row here but an `exit_code_mismatch` plus a `diff_mismatch` there. The
totals then differ by design — the bad output is a consequence of the crash, not
a second thing to investigate. Use `sp run error-summary` when you want the
per-error view.

With `--with-history`, each failure also gets a verdict across previous runs:
`NEW_REGRESSION`, `STILL_FAILING`, `NEVER_PASSED`, `FLAKY`, `NO_HISTORY`.

How far back that verdict can see depends on the sample. The history endpoint
pages over every regression test defined on a sample, so a test sharing its
sample with many others gets a shorter effective window than `--history-depth`
asks for. When that happens the verdict carries `window_truncated: true` and
`NEVER_PASSED` is reported at low confidence — it means "did not pass in the
runs visible here", not "has never passed". Check `prior_runs_considered` for
the window a verdict was actually based on.

### Finding a pull request's runs

`GET /runs` has no `pr_number` parameter — it filters on platform, branch,
commit, repository, status and a date window only. `sp run ls --pr N` therefore
pages newest-first and matches locally, capped by `--max-scan` (default 500).

That cap matters: a pull request whose last run predates the window comes back
empty, which would otherwise be indistinguishable from having no runs at all.
The payload carries `scanned` and `scan_truncated` so a caller can tell the two
apart, and raising `--max-scan` reaches further back.

```bash
sp run ls --pr 2309                        # recent PR, one page or two
sp run ls --pr 2109 --max-scan 1500        # months old, wider window
sp run ls --pr 2309 --platform linux       # server-side filters narrow the scan
```

When you already know the commit, `--commit <sha>` is filtered by the server and
is always cheaper.

### Reliability

Failed `GET`s are retried with exponential backoff — connection failures, read
timeouts, `429`, and `5xx`. This matters most for `investigate --with-history`,
which makes one call per failing sample: without it a single blip partway
through discards every lookup before it. Retry notices go to stderr, so JSON on
stdout stays clean. Tune with `--retries N`; `--retries 0` fails fast.

Writes are never retried. `POST /runs` is not idempotent, and a retry that
raced a slow-but-successful first attempt would queue the run twice.

### Exit codes

Scripts and agents can branch on the exit status:

| Code | Meaning |
|------|---------|
| 0 | success |
| 1 | unspecified error |
| 3 | could not reach the API |
| 4 | not found |
| 5 | validation error |
| 6 | authentication / authorization failure |
| 7 | rate limited |
| 8 | conflict (e.g. deleting a test that has results) |

## Development

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

isort . --check-only      # import order
pycodestyle .             # style
pydocstyle sp_cli         # docstrings
mypy sp_cli               # types
pytest                    # tests
```

## Relationship to the platform

`sp` is a **client**: it talks to the Sample Platform's REST API over HTTP. It is
deliberately kept in its own repository, separate from the platform server that
gets deployed on the VM. Point it at any deployment via `SP_BASE_URL`.
