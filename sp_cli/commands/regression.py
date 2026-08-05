"""``sp regression`` — list, inspect, and maintain regression-test definitions."""

from typing import Optional, Tuple

import click

from sp_cli.constants import (COMMAND_MAX_LENGTH, DESCRIPTION_MAX_LENGTH,
                              EXPECTED_RC_MAX, EXPECTED_RC_MIN, INPUT_TYPES,
                              OUTPUT_TYPES)
from sp_cli.runner import clean_params, fetch_and_render, send_and_render


@click.group()
def regression() -> None:
    """List, inspect, and maintain regression-test definitions."""


@regression.command('ls')
@click.option('--category', default=None, help='Filter by category name.')
@click.option('--tag', default=None, help='Filter by tag.')
@click.option('--active/--inactive', 'active', default=None,
              help='Select active or inactive tests (default: active only).')
@click.option('--sample-id', type=int, default=None, help='Filter by sample id.')
@click.option('--limit', type=int, default=None, help='Page size (max 100).')
@click.option('--offset', type=int, default=None, help='Pagination offset.')
@click.pass_context
def regression_ls(ctx: click.Context, category: Optional[str], tag: Optional[str],
                  active: Optional[bool], sample_id: Optional[int],
                  limit: Optional[int], offset: Optional[int]) -> None:
    """List regression-test definitions.

    The API's ``active`` filter is a two-way switch with no "everything"
    setting: omitting it lists active tests only, and ``--inactive`` lists
    inactive ones only. Run the command twice to see both.
    """
    params = clean_params({'category': category, 'tag': tag, 'active': active,
                           'sample_id': sample_id, 'limit': limit, 'offset': offset})
    fetch_and_render(ctx, '/regression-tests', params)


@regression.command('show')
@click.argument('regression_test_id', type=int)
@click.pass_context
def regression_show(ctx: click.Context, regression_test_id: int) -> None:
    """Show one regression test, including its expected outputs."""
    fetch_and_render(ctx, f'/regression-tests/{regression_test_id}')


@regression.command('create')
@click.option('--sample-id', type=int, required=True, help='Media sample this test runs against.')
@click.option('--command', required=True,
              help=f'CCExtractor arguments to run (max {COMMAND_MAX_LENGTH} chars).')
@click.option('--category', 'categories', multiple=True, required=True,
              help='Category name to file the test under (repeatable, at least one).')
@click.option('--input-type', type=click.Choice(INPUT_TYPES), default=None,
              help='How the sample is fed in. The API defaults to file.')
@click.option('--output-type', type=click.Choice(OUTPUT_TYPES), default=None,
              help='What the test produces. The API defaults to file.')
@click.option('--expected-rc', type=click.IntRange(EXPECTED_RC_MIN, EXPECTED_RC_MAX), default=None,
              help='Expected process exit code. The API defaults to 0.')
@click.option('--description', default=None,
              help=f'Free-text description (max {DESCRIPTION_MAX_LENGTH} chars).')
@click.option('--active', is_flag=True, default=False,
              help='Join the CI suite immediately. Off by default, matching the API.')
@click.pass_context
def regression_create(ctx: click.Context, sample_id: int, command: str,
                      categories: Tuple[str, ...], input_type: Optional[str],
                      output_type: Optional[str], expected_rc: Optional[int],
                      description: Optional[str], active: bool) -> None:
    """Create a regression test.

    A new test is created inactive unless --active is passed: the API's default
    is that a maintainer should see what it actually produces on a verification
    run before it joins the suite.

    Categories are given by name, not id, and every name must already exist --
    an unknown one is rejected rather than created implicitly.
    """
    if not 1 <= len(command) <= COMMAND_MAX_LENGTH:
        raise click.BadParameter(
            f'must be 1 to {COMMAND_MAX_LENGTH} characters', param_hint='--command')
    if description is not None and len(description) > DESCRIPTION_MAX_LENGTH:
        raise click.BadParameter(
            f'must be {DESCRIPTION_MAX_LENGTH} characters or less', param_hint='--description')

    body = clean_params({
        'sample_id': sample_id, 'command': command, 'categories': list(categories),
        'input_type': input_type, 'output_type': output_type,
        'expected_rc': expected_rc, 'description': description,
    })
    # Sent only when set: the API's load_default is False, so echoing that back
    # would be indistinguishable from asking for it.
    if active:
        body['active'] = True
    send_and_render(ctx, 'POST', '/regression-tests', body)


@regression.command('edit')
@click.argument('regression_test_id', type=int)
@click.option('--command', default=None, help=f'New command (max {COMMAND_MAX_LENGTH} chars).')
@click.option('--category', 'categories', multiple=True,
              help='Replace the category list (repeatable). Categories are replaced, not merged.')
@click.option('--input-type', type=click.Choice(INPUT_TYPES), default=None, help='New input type.')
@click.option('--output-type', type=click.Choice(OUTPUT_TYPES), default=None, help='New output type.')
@click.option('--expected-rc', type=click.IntRange(EXPECTED_RC_MIN, EXPECTED_RC_MAX), default=None,
              help='New expected exit code.')
@click.option('--description', default=None, help='New description.')
@click.option('--active/--inactive', 'active', default=None,
              help='Add the test to the CI suite or retire it.')
@click.pass_context
def regression_edit(ctx: click.Context, regression_test_id: int, command: Optional[str],
                    categories: Tuple[str, ...], input_type: Optional[str],
                    output_type: Optional[str], expected_rc: Optional[int],
                    description: Optional[str], active: Optional[bool]) -> None:
    """Update a regression test. Only the fields you pass are changed.

    --inactive is how you retire a test that has already run: it keeps the
    history that `sp regression rm` would refuse to erase.
    """
    if command is not None and not 1 <= len(command) <= COMMAND_MAX_LENGTH:
        raise click.BadParameter(
            f'must be 1 to {COMMAND_MAX_LENGTH} characters', param_hint='--command')
    if description is not None and len(description) > DESCRIPTION_MAX_LENGTH:
        raise click.BadParameter(
            f'must be {DESCRIPTION_MAX_LENGTH} characters or less', param_hint='--description')

    body = clean_params({
        'command': command, 'input_type': input_type, 'output_type': output_type,
        'expected_rc': expected_rc, 'description': description, 'active': active,
    })
    if categories:
        body['categories'] = list(categories)
    if not body:
        raise click.UsageError('Nothing to update: pass at least one field to change.')
    send_and_render(ctx, 'PATCH', f'/regression-tests/{regression_test_id}', body)


@regression.command('rm')
@click.argument('regression_test_id', type=int)
@click.option('--yes', is_flag=True, default=False, help='Skip the confirmation prompt.')
@click.pass_context
def regression_rm(ctx: click.Context, regression_test_id: int, yes: bool) -> None:
    """Delete a regression test that has never run.

    The API refuses (409, exit code 8) once any result references the test,
    because deleting it would erase evidence of past regressions. Retire such a
    test with `sp regression edit <id> --inactive` instead.
    """
    if not yes:
        click.confirm(f'Delete regression test {regression_test_id}?', abort=True)
    send_and_render(ctx, 'DELETE', f'/regression-tests/{regression_test_id}')
