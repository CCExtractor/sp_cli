"""``sp run`` — list, inspect, and triage CI runs."""

import base64
import sys
from typing import Any, Dict, List, Optional, Tuple

import click

from sp_cli.client import ApiError
from sp_cli.constants import (ARTIFACT_TYPES, CANCEL_REASON_MIN_LENGTH,
                              COMMIT_SHA_LENGTH, ERROR_GROUP_BY,
                              ERROR_SEVERITIES, ERROR_TYPES, INFRA_ERROR_TYPES,
                              LOG_CONTAINS_MAX_LENGTH, LOG_LEVELS,
                              LOG_MAX_LIMIT, LOG_SOURCES,
                              MAX_REGRESSION_TEST_IDS, PLATFORMS, RUN_STATUSES,
                              SAMPLE_STATUSES)
from sp_cli.output import render, render_error
from sp_cli.progress import Spinner
from sp_cli.runner import clean_params, fetch_and_render, send_and_render
from sp_cli.triage import classify_sample, is_failure


@click.group()
def run() -> None:
    """List, inspect, and triage CI runs."""


@run.command('ls')
@click.option('--status', type=click.Choice(RUN_STATUSES), default=None,
              help='Filter by lifecycle status. The API only tracks these three.')
@click.option('--platform', type=click.Choice(PLATFORMS), default=None, help='Test platform.')
@click.option('--branch', default=None, help='Filter by branch name.')
@click.option('--commit', 'commit_sha', default=None, help='Full 40-char commit SHA.')
@click.option('--repository', default=None, help='Filter by fork, as owner/repo.')
@click.option('--sort', default=None, help='Sort key, e.g. -created_at (default) or run_id.')
@click.option('--created-after', 'created_after', default=None,
              help='Only runs first seen at/after this time (ISO 8601).')
@click.option('--created-before', 'created_before', default=None,
              help='Only runs first seen at/before this time (ISO 8601).')
@click.option('--limit', type=int, default=None, help='Page size (max 100).')
@click.option('--offset', type=int, default=None, help='Pagination offset.')
@click.pass_context
def run_ls(ctx: click.Context, status: Optional[str], platform: Optional[str], branch: Optional[str],
           commit_sha: Optional[str], repository: Optional[str], sort: Optional[str],
           created_after: Optional[str], created_before: Optional[str],
           limit: Optional[int], offset: Optional[int]) -> None:
    """List CI runs (newest first).

    --status only accepts queued, running, and canceled: the API derives them
    from the latest TestProgress row, and pass/fail are per-sample outcomes
    rather than run states. Use `sp run summary` for a run's pass/fail split.
    """
    params = clean_params({'status': status, 'platform': platform, 'branch': branch,
                           'commit_sha': commit_sha, 'repository': repository, 'sort': sort,
                           'created_after': created_after, 'created_before': created_before,
                           'limit': limit, 'offset': offset})
    fetch_and_render(ctx, '/runs', params)


@run.command('create')
@click.option('--commit', 'commit_sha', required=True,
              help=f'Full {COMMIT_SHA_LENGTH}-char commit SHA. Short SHAs are rejected.')
@click.option('--platform', type=click.Choice(PLATFORMS), required=True, help='Test platform.')
@click.option('--repository', required=True, help='Fork to test, as owner/repo.')
@click.option('--branch', default=None,
              help='Branch name. The API defaults to master when omitted.')
@click.option('--pull-request', 'pull_request', type=int, default=None,
              help='Associate the run with a pull request number.')
@click.option('--test', 'regression_test_ids', type=int, multiple=True,
              help=f'Restrict to these regression test ids (repeatable, max {MAX_REGRESSION_TEST_IDS}). '
                   'Omit to run the full active suite.')
@click.pass_context
def run_create(ctx: click.Context, commit_sha: str, platform: str, repository: str,
               branch: Optional[str], pull_request: Optional[int],
               regression_test_ids: Tuple[int, ...]) -> None:
    """Queue a new CI run.

    The commit must already have a CI artifact built for that platform, so this
    schedules a test of an existing build rather than triggering a compile.
    """
    if len(commit_sha) != COMMIT_SHA_LENGTH or not all(c in '0123456789abcdefABCDEF' for c in commit_sha):
        raise click.BadParameter(
            f'must be a {COMMIT_SHA_LENGTH}-character hex string', param_hint='--commit')
    if '/' not in repository.strip('/') or repository.count('/') != 1:
        raise click.BadParameter('must be in owner/repo format', param_hint='--repository')
    if len(regression_test_ids) > MAX_REGRESSION_TEST_IDS:
        raise click.BadParameter(
            f'at most {MAX_REGRESSION_TEST_IDS} ids', param_hint='--test')

    body = clean_params({
        'commit_sha': commit_sha, 'platform': platform, 'repository': repository,
        'branch': branch, 'pull_request': pull_request,
        'regression_test_ids': list(regression_test_ids) or None,
    })
    send_and_render(ctx, 'POST', '/runs', body)


@run.command('show')
@click.argument('run_id', type=int)
@click.pass_context
def run_show(ctx: click.Context, run_id: int) -> None:
    """Show a single run's details."""
    fetch_and_render(ctx, f'/runs/{run_id}')


@run.command('summary')
@click.argument('run_id', type=int)
@click.pass_context
def run_summary(ctx: click.Context, run_id: int) -> None:
    """Show a run's pass/fail summary."""
    fetch_and_render(ctx, f'/runs/{run_id}/summary')


@run.command('failures')
@click.argument('run_id', type=int)
@click.pass_context
def run_failures(ctx: click.Context, run_id: int) -> None:
    """Show a run's failing tests, each labeled with a classification code."""
    client = ctx.obj['client']
    output = ctx.obj['output']
    try:
        with Spinner(f'Fetching results for run {run_id}', output != 'json'):
            samples = client.get_paginated(f'/runs/{run_id}/samples')
    except ApiError as error:
        render_error(error, output)
        raise SystemExit(error.exit_code)

    rows = [classify_sample(s) for s in samples if is_failure(s)]
    render({'data': rows, 'summary': {'failures': len(rows), 'of_total': len(samples)}},
           output, ctx.obj.get('color', False))


@run.command('results')
@click.argument('run_id', type=int)
@click.option('--status', type=click.Choice(SAMPLE_STATUSES), default=None,
              help='Filter by per-sample outcome.')
@click.option('--name', default=None, help='Substring match on the sample name.')
@click.option('--tag', default=None, help='Filter by sample tag.')
@click.option('--category', default=None, help='Filter by regression-test category.')
@click.option('--limit', type=int, default=None, help='Page size (max 100).')
@click.option('--offset', type=int, default=None, help='Pagination offset.')
@click.pass_context
def run_results(ctx: click.Context, run_id: int, status: Optional[str], name: Optional[str],
                tag: Optional[str], category: Optional[str],
                limit: Optional[int], offset: Optional[int]) -> None:
    """List all regression-test results in a run."""
    params = clean_params({'status': status, 'name': name, 'tag': tag, 'category': category,
                           'limit': limit, 'offset': offset})
    fetch_and_render(ctx, f'/runs/{run_id}/samples', params)


@run.command('result')
@click.argument('run_id', type=int)
@click.argument('sample_id', type=int)
@click.pass_context
def run_result(ctx: click.Context, run_id: int, sample_id: int) -> None:
    """Show full details for a single regression-test result in a run."""
    fetch_and_render(ctx, f'/runs/{run_id}/samples/{sample_id}')


@run.command('diff')
@click.argument('run_id', type=int)
@click.argument('sample_id', type=int)
@click.option('--regression', 'regression_id', type=int, default=None,
              help='Regression test id (auto-resolved if omitted).')
@click.option('--output', 'output_id', type=int, default=None,
              help='Output file id (auto-resolved if omitted).')
@click.option('--context', 'context_lines', type=int, default=None, help='Diff context lines.')
@click.pass_context
def run_diff(ctx: click.Context, run_id: int, sample_id: int, regression_id: Optional[int],
             output_id: Optional[int], context_lines: Optional[int]) -> None:
    """Show the expected-vs-actual diff for a failing result.

    Resolves the (regression, output) ids automatically when omitted, so you
    don't need the hidden ids the web UI requires to build a diff URL.
    """
    client = ctx.obj['client']
    output = ctx.obj['output']
    try:
        targets = _resolve_diff_targets(client, run_id, sample_id, regression_id, output_id)
        if not targets:
            raise ApiError('not_found', 'No differing output to diff for this result', 404)
        diffs = []
        for media_sample_id, reg_id, out_id in targets:
            params = clean_params({'context_lines': context_lines})
            diffs.append(client.get(
                f'/runs/{run_id}/samples/{media_sample_id}'
                f'/regression-tests/{reg_id}/outputs/{out_id}/diff',
                params=params))
    except ApiError as error:
        render_error(error, output)
        raise SystemExit(error.exit_code)

    render(diffs[0] if len(diffs) == 1 else {'data': diffs}, output, ctx.obj.get('color', False))


@run.command('approve-baseline')
@click.argument('run_id', type=int)
@click.argument('sample_id', type=int)
@click.option('--regression', 'regression_id', type=int, required=True,
              help='Regression test id of the result to approve.')
@click.option('--output', 'output_id', type=int, required=True,
              help="Output file id whose actual output becomes the new baseline.")
@click.option('--remove-variants', is_flag=True, default=False,
              help='Remove all platform-specific variants (see WARNING below).')
@click.pass_context
def run_approve_baseline(ctx: click.Context, run_id: int, sample_id: int,
                         regression_id: int, output_id: int, remove_variants: bool) -> None:
    """Approve a result's actual output as the new expected baseline.

    Requires admin privileges (the ``baselines:write`` scope).

    WARNING: --remove-variants makes this output the single source of truth
    across ALL platforms by deleting every platform-specific variant. This
    applies globally and cannot be undone from the CLI -- use with care.

    --regression and --output are required (no auto-resolution): approving a
    baseline is destructive, so the exact target must be stated explicitly.
    """
    client = ctx.obj['client']
    output = ctx.obj['output']
    try:
        # The endpoint's <sample_id> path slot is the *media* sample id, which
        # differs from the regression-result id passed on the command line.
        # Resolve it from the result detail (same contract as `run diff`).
        detail = client.get(f'/runs/{run_id}/samples/{sample_id}')
        media_sample_id = detail.get('sample_id')
        if media_sample_id is None:
            raise ApiError('not_found', 'Could not resolve the media sample for this result', 404)
        body = {'regression_id': regression_id, 'output_id': output_id,
                'remove_variants': remove_variants}
        result = client.request(
            'POST', f'/runs/{run_id}/samples/{media_sample_id}/baseline-approval',
            json_body=body)
    except ApiError as error:
        render_error(error, output)
        raise SystemExit(error.exit_code)

    render(result, output)


@run.command('progress')
@click.argument('run_id', type=int)
@click.option('--limit', type=int, default=None, help='Page size (max 100).')
@click.option('--offset', type=int, default=None, help='Pagination offset.')
@click.pass_context
def run_progress(ctx: click.Context, run_id: int, limit: Optional[int],
                 offset: Optional[int]) -> None:
    """Show the timeline of progress events the CI worker recorded for a run."""
    params = clean_params({'limit': limit, 'offset': offset})
    fetch_and_render(ctx, f'/runs/{run_id}/progress', params)


@run.command('config')
@click.argument('run_id', type=int)
@click.pass_context
def run_config(ctx: click.Context, run_id: int) -> None:
    """Show the platform, branch, commit, and regression tests a run was launched with."""
    fetch_and_render(ctx, f'/runs/{run_id}/config')


@run.command('cancel')
@click.argument('run_id', type=int)
@click.option('--reason', default=None,
              help=f'Why the run is being canceled (min {CANCEL_REASON_MIN_LENGTH} characters).')
@click.pass_context
def run_cancel(ctx: click.Context, run_id: int, reason: Optional[str]) -> None:
    """Cancel a queued or running test.

    Requires the ``runs:write`` scope. Idempotent: canceling a run that has
    already finished succeeds and reports ``status=no_op`` rather than failing.
    """
    client = ctx.obj['client']
    output = ctx.obj['output']
    if reason is not None and len(reason.strip()) < CANCEL_REASON_MIN_LENGTH:
        raise click.BadParameter(
            f'must be at least {CANCEL_REASON_MIN_LENGTH} characters', param_hint='--reason')
    body = {'reason': reason} if reason else None
    try:
        result = client.request('POST', f'/runs/{run_id}/cancel', json_body=body)
    except ApiError as error:
        render_error(error, output)
        raise SystemExit(error.exit_code)
    render(result, output)


@run.command('output')
@click.argument('run_id', type=int)
@click.argument('sample_id', type=int)
@click.option('--regression', 'regression_id', type=int, default=None,
              help='Regression test id (auto-resolved if omitted).')
@click.option('--output', 'output_id', type=int, default=None,
              help='Output file id (auto-resolved if omitted).')
@click.option('--side', type=click.Choice(('expected', 'actual')), default='actual',
              show_default=True, help='Which side of the comparison to fetch.')
@click.option('--format', 'fmt', default=None, help='Response format accepted by the API.')
@click.option('--decode', is_flag=True, default=False,
              help='Write the decoded file to stdout instead of the JSON envelope.')
@click.pass_context
def run_output(ctx: click.Context, run_id: int, sample_id: int, regression_id: Optional[int],
               output_id: Optional[int], side: str, fmt: Optional[str], decode: bool) -> None:
    """Fetch one side of a result's output file.

    Resolves the (media sample, regression, output) ids the same way `sp run
    diff` does, so the hidden ids the web UI needs are not required here.

    The API returns the file base64-encoded inside a JSON envelope. --decode
    writes the decoded bytes straight to stdout instead, so the subtitle file
    can be read or redirected:

        sp run output 9388 11 --decode > actual.srt

    Note: for an output that matched, the API answers ``actual`` with a 303
    redirect to ``expected`` -- requests follows it, so the expected content is
    what comes back.
    """
    client = ctx.obj['client']
    output = ctx.obj['output']
    try:
        targets = _resolve_diff_targets(client, run_id, sample_id, regression_id, output_id)
        if not targets:
            raise ApiError('not_found', 'No output to fetch for this result', 404)
        media_sample_id, reg_id, out_id = targets[0]
        payload = client.get(
            f'/runs/{run_id}/samples/{media_sample_id}'
            f'/regression-tests/{reg_id}/outputs/{out_id}/{side}',
            params=clean_params({'format': fmt}))
        if decode:
            _write_decoded(payload)
            return
    except ApiError as error:
        render_error(error, output)
        raise SystemExit(error.exit_code)
    render(payload, output)


def _write_decoded(payload: Any) -> None:
    """
    Write an output envelope's file content to stdout as raw bytes.

    Written to the binary buffer rather than echoed, so the file survives
    byte-for-byte: these are subtitle files that may carry CRLF line endings
    and a non-UTF-8 encoding, and re-encoding them would corrupt a diff.

    :param payload: The decoded output envelope from the API.
    :type payload: Any
    :raises ApiError: when the envelope carries no inline content.
    """
    content = payload.get('content') if isinstance(payload, dict) else None
    if content is None:
        raise ApiError(
            'no_content',
            'This output has no inline content to decode; '
            'fetch it from download_url instead.', 404,
            {'download_url': payload.get('download_url') if isinstance(payload, dict) else None})

    if (payload.get('encoding') or '').lower() == 'base64':
        data = base64.b64decode(content)
    else:
        data = str(content).encode('utf-8')

    stream = getattr(sys.stdout, 'buffer', None)
    if stream is None:  # a text-only stream, e.g. under a test runner
        sys.stdout.write(data.decode('utf-8', errors='replace'))
    else:
        stream.write(data)
        stream.flush()


@run.command('artifacts')
@click.argument('run_id', type=int)
@click.option('--type', 'artifact_type', type=click.Choice(ARTIFACT_TYPES), default=None,
              help='Filter by artifact type.')
@click.option('--limit', type=int, default=None, help='Page size (max 100).')
@click.option('--offset', type=int, default=None, help='Pagination offset.')
@click.pass_context
def run_artifacts(ctx: click.Context, run_id: int, artifact_type: Optional[str],
                  limit: Optional[int], offset: Optional[int]) -> None:
    """List downloadable artifacts for a run.

    Covers the build binary, any coredump, combined stdout, the build log, and
    every expected/actual output file. `storage_status` says whether the blob is
    actually retrievable; `download_url` is null for the build log, which is read
    through `sp run logs` instead of downloaded.
    """
    params = clean_params({'type': artifact_type, 'limit': limit, 'offset': offset})
    fetch_and_render(ctx, f'/runs/{run_id}/artifacts', params)


@run.command('logs')
@click.argument('run_id', type=int)
@click.option('--level', type=click.Choice(LOG_LEVELS), default=None,
              help='Keep lines at this level. Levels are scanned out of the raw text.')
@click.option('--source', type=click.Choice(LOG_SOURCES), default=None,
              help='Keep lines from this component.')
@click.option('--contains', default=None,
              help=f'Case-insensitive substring filter (max {LOG_CONTAINS_MAX_LENGTH} chars).')
@click.option('--limit', type=int, default=None,
              help=f'Lines per page (max {LOG_MAX_LIMIT}, default 100).')
@click.option('--cursor', default=None, help='Resume from a previous response\'s next_cursor.')
@click.option('--all', 'fetch_all', is_flag=True, default=False,
              help='Follow next_cursor and return the whole log at once.')
@click.pass_context
def run_logs(ctx: click.Context, run_id: int, level: Optional[str], source: Optional[str],
             contains: Optional[str], limit: Optional[int], cursor: Optional[str],
             fetch_all: bool) -> None:
    """Read a run's build log (requires the system:read scope).

    This endpoint is cursor-paginated, not offset-paginated: page forward with
    --cursor using the next_cursor from the previous response, or pass --all to
    follow it to the end. Sending an offset is a 400.

    A 404 with code `log_not_found` means the log is no longer on the VM's disk
    rather than that the run is missing -- fetch it from `sp run artifacts
    --type build_log` in that case.

    The level and source of each line are recovered by scanning the raw text, so
    they are best-effort: an unrecognized line reports level=info, source=web.
    """
    if contains is not None and len(contains) > LOG_CONTAINS_MAX_LENGTH:
        raise click.BadParameter(
            f'must be {LOG_CONTAINS_MAX_LENGTH} characters or less', param_hint='--contains')
    if fetch_all and cursor is not None:
        raise click.BadParameter('--all starts from the beginning; drop --cursor',
                                 param_hint='--cursor')

    params = clean_params({'level': level, 'source': source, 'contains': contains,
                           'limit': limit, 'cursor': cursor})
    if not fetch_all:
        fetch_and_render(ctx, f'/runs/{run_id}/logs', params)
        return

    client = ctx.obj['client']
    output = ctx.obj['output']
    try:
        with Spinner(f'Reading log for run {run_id}', output != 'json'):
            lines = client.get_cursor_paginated(f'/runs/{run_id}/logs', params)
    except ApiError as error:
        render_error(error, output)
        raise SystemExit(error.exit_code)
    render({'data': lines, 'summary': {'lines': len(lines)}}, output, ctx.obj.get('color', False))


@run.command('errors')
@click.argument('run_id', type=int)
@click.option('--type', 'error_type', type=click.Choice(ERROR_TYPES), default=None,
              help='Filter by error type.')
@click.option('--severity', type=click.Choice(ERROR_SEVERITIES), default=None,
              help='Filter by severity. Test errors are only error or warning.')
@click.option('--sample', 'sample_id', type=int, default=None,
              help='Restrict to one media sample id.')
@click.option('--limit', type=int, default=None, help='Page size (max 100).')
@click.option('--offset', type=int, default=None, help='Pagination offset.')
@click.pass_context
def run_errors(ctx: click.Context, run_id: int, error_type: Optional[str],
               severity: Optional[str], sample_id: Optional[int],
               limit: Optional[int], offset: Optional[int]) -> None:
    """Show structured test errors for a run.

    These are derived from the result rows at request time rather than stored,
    so they line up with `sp run failures` but carry per-output detail. For
    failures of the infrastructure itself, see `sp run infra-errors`.
    """
    params = clean_params({'type': error_type, 'severity': severity, 'sample_id': sample_id,
                           'limit': limit, 'offset': offset})
    fetch_and_render(ctx, f'/runs/{run_id}/errors', params)


@run.command('error-summary')
@click.argument('run_id', type=int)
@click.option('--group-by', 'group_by', type=click.Choice(ERROR_GROUP_BY), default=None,
              help='Bucket key. The API defaults to type.')
@click.option('--severity', type=click.Choice(ERROR_SEVERITIES), default=None,
              help='Keep only buckets at this severity.')
@click.option('--limit', type=int, default=None, help='Page size (max 100).')
@click.option('--offset', type=int, default=None, help='Pagination offset.')
@click.pass_context
def run_error_summary(ctx: click.Context, run_id: int, group_by: Optional[str],
                      severity: Optional[str], limit: Optional[int],
                      offset: Optional[int]) -> None:
    """Show grouped error counts for a run.

    The cheapest first look at a broken run: one row per bucket with a count,
    so an agent can decide what to drill into before pulling every error.
    Each bucket's severity is the highest of the errors within it.
    """
    params = clean_params({'group_by': group_by, 'severity': severity,
                           'limit': limit, 'offset': offset})
    fetch_and_render(ctx, f'/runs/{run_id}/error-summary', params)


@run.command('infra-errors')
@click.argument('run_id', type=int)
@click.option('--type', 'error_type', type=click.Choice(INFRA_ERROR_TYPES), default=None,
              help='Filter by infrastructure failure stage.')
@click.option('--severity', type=click.Choice(ERROR_SEVERITIES), default=None,
              help='Filter by severity. These are always reported as critical.')
@click.option('--include-stack', is_flag=True, default=False,
              help='Include stack traces (admin or contributor only; 403 otherwise).')
@click.option('--limit', type=int, default=None, help='Page size (max 100).')
@click.option('--offset', type=int, default=None, help='Pagination offset.')
@click.pass_context
def run_infra_errors(ctx: click.Context, run_id: int, error_type: Optional[str],
                     severity: Optional[str], include_stack: bool,
                     limit: Optional[int], offset: Optional[int]) -> None:
    """Show infrastructure errors for a run (requires the system:read scope).

    These are VM, checkout, build, worker, and storage failures, classified from
    TestProgress messages by keyword -- so the type is a best-effort guess, not a
    recorded field. A run that fails here usually has no test errors at all.

    Stack traces are withheld by default because they carry internal paths;
    --include-stack needs the admin or contributor role and 403s without it.
    """
    params = clean_params({'type': error_type, 'severity': severity,
                           'limit': limit, 'offset': offset})
    if include_stack:
        params['include_stack'] = 'true'
    fetch_and_render(ctx, f'/runs/{run_id}/infrastructure-errors', params)


def _resolve_diff_targets(client: Any, run_id: int, sample_id: int,
                          regression_id: Optional[int],
                          output_id: Optional[int]) -> List[Tuple[int, int, int]]:
    """
    Resolve the (media_sample_id, regression_id, output_id) triples to diff.

    The diff endpoint is keyed by the *media* sample id, the regression test
    id, and the output file id -- three different numbers. The CLI's
    ``sample_id`` argument is the result id within the run (the regression test
    id), so we always fetch the result detail to recover the media sample id
    (``detail['sample_id']``) and the differing output(s), and let the caller
    pass explicit ids only to narrow which output(s) to diff.

    :param client: The API client.
    :type client: Any
    :param run_id: The run id.
    :type run_id: int
    :param sample_id: The result id within the run (the regression test id).
    :type sample_id: int
    :param regression_id: Optional explicit regression id override.
    :type regression_id: Optional[int]
    :param output_id: Optional explicit output id to diff.
    :type output_id: Optional[int]
    :return: A list of ``(media_sample_id, regression_id, output_id)`` triples.
    :rtype: List[Tuple[int, int, int]]
    """
    detail = client.get(f'/runs/{run_id}/samples/{sample_id}')
    media_sample_id = detail.get('sample_id')
    reg_id = regression_id if regression_id is not None else detail.get('regression_test_id')
    if media_sample_id is None or reg_id is None:
        return []

    outputs = detail.get('outputs') or []
    # The API reports per-output status as pass|fail|missing_output; anything
    # not 'pass' is worth diffing. Fall back to all outputs if none qualify.
    differing = [o for o in outputs if o.get('status') not in (None, 'pass')] or outputs

    if output_id is not None:
        return [(media_sample_id, reg_id, output_id)]
    return [(media_sample_id, reg_id, o.get('output_id')) for o in differing
            if o.get('output_id') is not None]
