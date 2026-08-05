"""``sp category`` — list and maintain the categories regression tests are filed under."""

from typing import Optional

import click

from sp_cli.constants import DESCRIPTION_MAX_LENGTH, NAME_MAX_LENGTH
from sp_cli.runner import clean_params, fetch_and_render, send_and_render


@click.group()
def category() -> None:
    """List and maintain regression-test categories."""


@category.command('ls')
@click.option('--limit', type=int, default=None, help='Page size (max 100).')
@click.option('--offset', type=int, default=None, help='Pagination offset.')
@click.pass_context
def category_ls(ctx: click.Context, limit: Optional[int], offset: Optional[int]) -> None:
    """List categories alphabetically, each with how many tests reference it."""
    fetch_and_render(ctx, '/categories', clean_params({'limit': limit, 'offset': offset}))


@category.command('create')
@click.argument('name')
@click.option('--description', default=None,
              help=f'Free-text description (max {DESCRIPTION_MAX_LENGTH} chars).')
@click.pass_context
def category_create(ctx: click.Context, name: str, description: Optional[str]) -> None:
    """Create a category. Names are unique, so a duplicate is a 409 (exit code 8)."""
    _check_widths(name, description)
    send_and_render(ctx, 'POST', '/categories',
                    clean_params({'name': name, 'description': description}))


@category.command('edit')
@click.argument('category_id', type=int)
@click.option('--name', default=None, help=f'New name (max {NAME_MAX_LENGTH} chars).')
@click.option('--description', default=None, help='New description.')
@click.pass_context
def category_edit(ctx: click.Context, category_id: int, name: Optional[str],
                  description: Optional[str]) -> None:
    """Rename a category or change its description. Only the fields you pass change."""
    _check_widths(name, description)
    body = clean_params({'name': name, 'description': description})
    if not body:
        raise click.UsageError('Nothing to update: pass --name or --description.')
    send_and_render(ctx, 'PATCH', f'/categories/{category_id}', body)


@category.command('rm')
@click.argument('category_id', type=int)
@click.option('--yes', is_flag=True, default=False, help='Skip the confirmation prompt.')
@click.pass_context
def category_rm(ctx: click.Context, category_id: int, yes: bool) -> None:
    """Delete a category no regression test references.

    The API refuses (409, exit code 8) while tests still point at it, since
    dropping it would silently change which tests a suite selection picks up.
    Detach them first with `sp regression edit <id> --category ...`.
    """
    if not yes:
        click.confirm(f'Delete category {category_id}?', abort=True)
    send_and_render(ctx, 'DELETE', f'/categories/{category_id}')


def _check_widths(name: Optional[str], description: Optional[str]) -> None:
    """
    Reject over-long names and descriptions before the request goes out.

    :param name: The proposed category name, if any.
    :type name: Optional[str]
    :param description: The proposed description, if any.
    :type description: Optional[str]
    :raises click.BadParameter: when either exceeds the API's column width.
    """
    if name is not None and not 1 <= len(name) <= NAME_MAX_LENGTH:
        raise click.BadParameter(
            f'must be 1 to {NAME_MAX_LENGTH} characters', param_hint='--name')
    if description is not None and len(description) > DESCRIPTION_MAX_LENGTH:
        raise click.BadParameter(
            f'must be {DESCRIPTION_MAX_LENGTH} characters or less', param_hint='--description')
