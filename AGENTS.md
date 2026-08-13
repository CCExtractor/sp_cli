# Driving `sp` as an agent

`sp` is a CLI client for the CCExtractor Sample Platform REST API. It exists so a
CI failure can be investigated from the terminal instead of by clicking through
the web frontend. Output is JSON by default, so every command pipes into `jq`.

This file is for an AI agent (or anyone scripting the tool). Humans want
[README.md](README.md).

## Setup

```bash
pip install -e .   # or: pip install git+https://github.com/CCExtractor/sp_cli
sp auth login --email you@example.com --scope runs:read --scope results:read --scope system:read
```

`sp` points at `https://sampleplatform.ccextractor.org/api/v1` by default;
override with `SP_BASE_URL` or `--base-url` for another deployment.

The token is saved to `~/.config/sp/config.json` (mode `0600`). Precedence is
`--token` > `SP_API_TOKEN` > that file, and the same for the host:
`--base-url` > `SP_BASE_URL` > the URL saved at login > the default.

**Ask for read scopes only.** With no write scope the tool physically cannot
change anything, which is what you want when an agent is driving. Grant
`system:read` explicitly — omitting `--scope` gets you `runs:read` and
`results:read` only, and `run logs` / `run infra-errors` 403 without it.

Login requires an existing Sample Platform account; there is no sign-up here,
and everything except `sp health` needs one. `baselines:write`, `system:write`
and `tokens:manage` are admin-only and are refused at login for other roles.

## Safety rules

- **Never run writes against production.** `sp admin pause` stops CI for every
  contributor; `sp run create` burns real VM time; `sp regression rm` /
  `category rm` destroy shared configuration. A read-only token makes all of
  this impossible — use one.
- The read commands (`run ls/show/summary/failures/result/results/diff/logs/
  errors/error-summary/infra-errors/artifacts/progress/config/output`,
  `sample *`, `regression ls/show`, `category ls`, `investigate`, `queue`,
  `health`, `auth whoami/tokens`) are always safe.
- Anything named `create`, `edit`, `rm`, `cancel`, `approve-baseline`, `pause`,
  `resume`, `set-role`, `add`, or `revoke` writes. Ask a human first.

## Start here

```bash
sp investigate <run_id>
```

One call gives the run header, pass/fail counts, and every failure classified
with a stable code — `SEGFAULT`, `ABORT`, `TIMEOUT`, `EXIT_CODE_MISMATCH`,
`MISSING_OUTPUT`, `OUTPUT_DIFF` — plus a confidence and a plain-English reason.
For most questions this is the whole answer. Add `-o table` when a human will
read it.

Then drill in:

```bash
sp run compare <run> <baseline>       # which failures are new vs the baseline
sp run summary <run>                  # counts only, cheapest
sp run error-summary <run>            # grouped error counts, server-derived
sp run result <run> <regression_test_id>   # one test: exit code, command, outputs
sp run diff <run> <regression_test_id>     # expected vs actual, structured hunks
sp run logs <run> --all               # build log (needs system:read)
sp run infra-errors <run>             # VM / checkout / build / storage problems
sp run artifacts <run>                # binary, coredump, stdout, outputs
sp queue                              # what is running right now
sp run progress <run>                 # live status of one run
sp run ls --pr <n>                    # every run for one pull request
sp run ls --created-after 2026-08-09T00:00:00Z   # recent activity
```

`--pr` is filtered client-side — the API has no `pr_number` parameter — so it
scans the newest `--max-scan` runs (default 500) and matches locally. Check
`scan_truncated` in the payload before concluding a pull request has no runs:
`true` means the window ran out, not that nothing exists. Old pull requests need
a bigger `--max-scan`; `--commit <sha>` is server-side and always cheaper when
you know the SHA.

## A worked recipe: is this failure new?

This is the question that actually matters on a PR, and it is comparative — a
single run cannot answer it. A real example: a reviewer reported "No output
generated but there should be" on run 9410.

1. **Diff against a baseline run.**
   ```bash
   sp run compare 9410 9398
   ```
   23 `new`, 44 `still_failing`, 0 `fixed`. The 44 are the platform's standing
   baseline — nobody's fault, present on other PRs too. The 23 are what this
   change actually did.

   Read `not_rerun` before you read `fixed`: a test that produced no result has
   not been repaired, and a run that died early would otherwise look like a
   clean sweep.

2. **Compare platforms for the same commit.** Runs come in linux/windows pairs.
   ```bash
   sp investigate 9410   # linux:   45 MISSING_OUTPUT
   sp investigate 9411   # windows: 24 MISSING_OUTPUT
   ```
   Same commit, different result ⇒ it is not the source code.

3. **Check the exit codes.** All the new failures exited `0` — the program
   reported success, so "no output" was not a crash.

4. **Read the log.** It contained exactly 21 `(500) INTERNAL SERVER ERROR`
   lines, each inside a test entry that finished with exit code 0; the windows
   log had none. The count matching the failure count pinned the cause to the
   server, not the tests.

The general shape: **counts are a hypothesis, set-diffs are evidence.** Two runs
having the same number of failures does not mean the same tests failed. `run
compare` does this properly; doing it by hand with `jq` is easy to get subtly
wrong.

## Reading the output

Collections come back as `{"data": [...], "total": N, ...}`; single objects come
back bare. `investigate` is the exception: it returns `{run, summary, by_code,
failures}`, so its rows are under **`failures`**, while `run failures` — like
every other list — puts them under **`data`**.

```bash
sp investigate 9412   | jq -r '.failures[] | "\(.code)\t\(.sample_name)"'
sp run failures 9412  | jq -r '.data[]     | "\(.code)\t\(.sample_name)"'
sp run ls --limit 5   | jq -r '.data[]     | "\(.run_id) \(.platform) \(.status)"'
```

Exit codes to branch on: `0` ok, `3` unreachable, `4` not found, `5` validation,
`6` auth, `7` rate limited, `8` conflict.

Progress spinners, retry notices, and colour go to **stderr** and are suppressed
when stdout is not a terminal, so JSON on stdout is always parseable.

## Gotchas that will otherwise cost you time

- **`--with-history` is unusable against production** until
  [sample-platform#1161](https://github.com/CCExtractor/sample-platform/issues/1161)
  is fixed: `/samples/{id}/history` paginates in Python after loading a sample's
  entire history, so even `?limit=5` times out. The flag degrades gracefully —
  rows come back `UNKNOWN` — but the verdicts are worthless there. Skip it.
- **Failure rows and `error_count` are different units.** `investigate` reports
  one row per failing sample; `run summary`'s `error_count` counts errors. A test
  that both crashes and writes a wrong output is one `SEGFAULT` row but two
  errors. Do not report this as an off-by-one.
- **`/runs?status=` only accepts `queued|running|canceled`.** Those are run
  states derived from progress rows; `pass`/`fail` are per-sample outcomes.
  Anything else is a 400.
- **`run logs` is cursor-paginated**, everything else is offset-paginated. Use
  `--all` or `--cursor`, never `--offset`.
- **There are no per-sample logs.** `/runs/{id}/samples/{sid}/logs` is a
  permanent 404 by design — the worker does not store them. There is
  deliberately no command for it; do not add one.
- **A missing build log 404s as `log_not_found`**, distinct from a missing run's
  `not_found`. Both exit 4, so branch on `error.code`, not the status.
- **Log `level` and `source` are guessed** by scanning line text, not recorded
  fields. An unmatched line reports `level=info`, `source=web`. Do not treat
  `[ERROR]` in a build log as authoritative — ffmpeg banner output shows up
  there too.
- **`include_stack=true` on infra errors needs admin or contributor role** and
  403s otherwise.

## When something looks wrong with the CLI itself

Check whether the API agrees before assuming the CLI is at fault:
`run error-summary` is derived server-side and is an independent cross-check on
the classifier. If the two disagree in a way the "different units" note above
does not explain, that is a real bug worth reporting.
