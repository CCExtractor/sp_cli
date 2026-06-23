# sp — CCExtractor Sample Platform CLI

`sp` is a command-line client for the [CCExtractor Sample Platform](https://github.com/CCExtractor/sample-platform)
REST API. It lets a developer **or an AI agent** investigate CI runs end-to-end
from the terminal — no web frontend required.

Output defaults to **JSON** (ideal for agents and scripts), with a human-friendly
`-o table` view.

## Install

```bash
pip install -e .
```

This installs the `sp` command.

## Configure

`sp` needs to know where the API lives and (optionally) a bearer token:

```bash
export SP_BASE_URL=https://sampleplatform.ccextractor.org/api/v1   # or your instance
export SP_API_TOKEN=<your-token>                                   # if the API requires auth
```

Both can also be passed per-command with `--base-url` and `--token`.

## Usage

```bash
sp                              # banner / help
sp health                       # API + dependency health
sp run ls                       # list CI runs
sp run summary <run_id>         # pass/fail summary for a run
sp run failures <run_id>        # failing tests, each auto-classified
sp run diff <run_id> <id>       # expected-vs-actual diff for a result
sp run logs <run_id>            # raw run logs
sp investigate <run_id>         # one-shot triage: info + counts + classified failures
```

Add `-o table` to any command for a human-readable view (default is JSON):

```bash
sp -o table investigate 9299
```

### The classifier

`sp` labels each failure with a stable code — `SEGFAULT`, `ABORT`, `TIMEOUT`,
`EXIT_CODE_MISMATCH`, `MISSING_OUTPUT`, `OUTPUT_DIFF`, `PASS` — so a person or an
agent gets a straight answer about *why* a test failed, without reading logs.

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
