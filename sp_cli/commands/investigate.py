"""``sp investigate`` — one-shot triage of a run (status + counts + classified failures)."""

from typing import Any, Dict, List, Optional

import click

from sp_cli.client import ApiError
from sp_cli.history import (NEW_REGRESSION, classify_history, group_by_verdict,
                            split_history, unknown_history)
from sp_cli.output import render, render_error
from sp_cli.runner import clean_params
from sp_cli.triage import classify_sample, group_by_code, is_failure

_RUN_FIELDS = ('run_id', 'pr_number', 'platform', 'commit_sha', 'branch', 'status', 'github_link')

#: How many prior runs to weigh per failure when --with-history is used. Deep
#: enough to see a sample settle, shallow enough to keep it one call per sample.
DEFAULT_HISTORY_DEPTH = 20


@click.command('investigate')
@click.argument('run_id', type=int)
@click.option('--with-history', 'with_history', is_flag=True, default=False,
              help='Label each failure as a new regression, long-standing, or never-passing.')
@click.option('--history-depth', type=int, default=None,
              help=f'Prior runs to weigh per failure (default: {DEFAULT_HISTORY_DEPTH}). '
                   'Implies --with-history.')
@click.pass_context
def investigate(ctx: click.Context, run_id: int, with_history: bool,
                history_depth: Optional[int]) -> None:
    """Triage a run in one shot: run info, pass/fail counts, and classified failures.

    Combines the run detail, summary, and per-result classification into a single
    report -- the whole "what failed and why" investigation in one command.

    --with-history answers the question the codes alone cannot: is this failure
    new? It adds a `history` block to every failure plus a `by_verdict` tally,
    at the cost of one extra API call per distinct sample. NEW_REGRESSION means
    the test passed in the previous run, which is where to start reading.
    """
    client = ctx.obj['client']
    output = ctx.obj['output']
    if history_depth is not None:
        with_history = True
    depth = history_depth if history_depth is not None else DEFAULT_HISTORY_DEPTH

    try:
        run = client.get(f'/runs/{run_id}')
        summary = client.get(f'/runs/{run_id}/summary')
        samples = client.get_paginated(f'/runs/{run_id}/samples')
    except ApiError as error:
        render_error(error, output)
        raise SystemExit(error.exit_code)

    failures = [classify_sample(s) for s in samples if is_failure(s)]
    report: Dict[str, Any] = {
        'run': {field: run.get(field) for field in _RUN_FIELDS},
        'summary': summary,
        'by_code': group_by_code(failures),
        'failures': failures,
    }

    if with_history:
        try:
            _attach_history(client, failures, run_id, run.get('platform'), depth)
        except ApiError as error:
            render_error(error, output)
            raise SystemExit(error.exit_code)
        report['by_verdict'] = group_by_verdict(failures)

    if output == 'json':
        render(report, 'json')
    else:
        _print_digest(report, with_history)


def _attach_history(client: Any, failures: List[Dict[str, Any]], run_id: int,
                    platform: Optional[str], depth: int) -> None:
    """
    Add a ``history`` verdict block to every failure row, in place.

    History is fetched per *sample*, but several regression tests can share one
    sample, so responses are cached by sample id and then narrowed per failure
    by regression test id. Restricting to the run's own platform keeps a Windows
    failure from being judged against Linux history.

    :param client: The API client.
    :type client: Any
    :param failures: Classified failure rows, mutated in place.
    :type failures: List[Dict[str, Any]]
    :param run_id: The run being investigated.
    :type run_id: int
    :param platform: The run's platform, used to filter history.
    :type platform: Optional[str]
    :param depth: How many prior runs to consider per failure.
    :type depth: int
    """
    cache: Dict[int, List[Dict[str, Any]]] = {}
    for failure in failures:
        sample_id = failure.get('sample_id')
        if not isinstance(sample_id, int):
            failure['history'] = unknown_history('Result has no sample id to look up')
            continue

        if sample_id not in cache:
            # +1 so the current run's own entry cannot displace an older one.
            params = clean_params({'platform': platform, 'limit': depth + 1})
            cache[sample_id] = client.get_paginated(
                f'/samples/{sample_id}/history', params=params, max_items=depth + 1)

        current, prior = split_history(cache[sample_id], run_id,
                                       failure.get('regression_test_id'))
        failure['history'] = classify_history(current, prior[:depth])


def _print_digest(report: Dict[str, Any], with_history: bool = False) -> None:
    """
    Print a human-readable investigation digest.

    :param report: The assembled investigation report.
    :type report: Dict[str, Any]
    :param with_history: Whether history verdicts were collected.
    :type with_history: bool
    """
    run = report['run']
    summary = report['summary']
    header = (f"Run {run.get('run_id')} · PR {run.get('pr_number')} · {run.get('platform')} · "
              f"{run.get('commit_sha')} · {str(run.get('status')).upper()}")
    click.echo(header)
    click.echo(f"  {summary.get('fail_count')} failed / {summary.get('total_samples')} total"
               f"  ({summary.get('pass_count')} pass)")

    by_code = report['by_code']
    if by_code:
        click.echo()
        click.echo("  by code:")
        for code, count in by_code.items():
            click.echo(f"    {str(count).rjust(4)}  {code}")

    by_verdict = report.get('by_verdict')
    if by_verdict:
        click.echo()
        click.echo("  by history:")
        for verdict, count in by_verdict.items():
            click.echo(f"    {str(count).rjust(4)}  {verdict}")

    failures: List[Dict[str, Any]] = report['failures']
    if failures:
        click.echo()
        render({'data': [_flatten(f, with_history) for f in failures]}, 'table')

    _print_regressions(failures, with_history)


def _print_regressions(failures: List[Dict[str, Any]], with_history: bool) -> None:
    """
    Call out the failures that were passing in the previous run.

    :param failures: Classified failure rows.
    :type failures: List[Dict[str, Any]]
    :param with_history: Whether history verdicts were collected.
    :type with_history: bool
    """
    if not with_history:
        return
    regressions = [f for f in failures
                   if f.get('history', {}).get('verdict') == NEW_REGRESSION]
    if not regressions:
        return
    click.echo()
    click.echo(f"  {len(regressions)} of these were passing in the previous run:")
    for failure in regressions:
        click.echo(f"    {failure.get('sample_name')} — {failure['history']['reason']}")


def _flatten(failure: Dict[str, Any], with_history: bool) -> Dict[str, Any]:
    """
    Flatten a failure row for the table view, lifting the verdict into a column.

    The nested ``history`` block is right for JSON and wrong for a table, which
    needs scalar cells.

    :param failure: One classified failure row.
    :type failure: Dict[str, Any]
    :param with_history: Whether to add the verdict column.
    :type with_history: bool
    :return: A flat row safe to render as a table.
    :rtype: Dict[str, Any]
    """
    if not with_history:
        return failure
    row = {key: value for key, value in failure.items() if key != 'history'}
    row['verdict'] = failure.get('history', {}).get('verdict')
    return row
