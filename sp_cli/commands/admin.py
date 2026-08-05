"""``sp admin`` — platform configuration: maintenance, blocked users, forbidden extensions.

Every command here needs the admin role, and the write ones additionally need
the ``system:write`` scope. A token without them gets a 403 (exit code 6).
"""

from typing import Optional

import click

from sp_cli.constants import (BLOCKED_USER_COMMENT_MAX_LENGTH,
                              EXTENSION_MAX_LENGTH, PLATFORMS)
from sp_cli.runner import clean_params, fetch_and_render, send_and_render


@click.group()
def admin() -> None:
    """Inspect and change platform configuration (admin only)."""


@admin.command('maintenance')
@click.pass_context
def admin_maintenance(ctx: click.Context) -> None:
    """Show whether CI is paused, one entry per platform.

    Answers under a ``platforms`` key rather than the usual ``data`` collection
    envelope: the list comes from the platform enum, not a growable table, so
    there is nothing to paginate.
    """
    fetch_and_render(ctx, '/system/maintenance')


@admin.command('pause')
@click.argument('platform', type=click.Choice(PLATFORMS))
@click.pass_context
def admin_pause(ctx: click.Context, platform: str) -> None:
    """Stop handing new runs to this platform's VMs.

    Runs still queue while a platform is paused -- they are simply not
    dispatched until it is resumed.
    """
    send_and_render(ctx, 'PATCH', f'/system/maintenance/{platform}', {'disabled': True})


@admin.command('resume')
@click.argument('platform', type=click.Choice(PLATFORMS))
@click.pass_context
def admin_resume(ctx: click.Context, platform: str) -> None:
    """Resume dispatching runs to this platform's VMs."""
    send_and_render(ctx, 'PATCH', f'/system/maintenance/{platform}', {'disabled': False})


@admin.group('blocked-users')
def blocked_users() -> None:
    """List, block, and unblock GitHub accounts."""


@blocked_users.command('ls')
@click.option('--limit', type=int, default=None, help='Page size (max 100).')
@click.option('--offset', type=int, default=None, help='Pagination offset.')
@click.pass_context
def blocked_users_ls(ctx: click.Context, limit: Optional[int], offset: Optional[int]) -> None:
    """List the GitHub accounts blocked from triggering CI runs."""
    fetch_and_render(ctx, '/system/blocked-users',
                     clean_params({'limit': limit, 'offset': offset}))


@blocked_users.command('add')
@click.argument('user_id', type=int)
@click.option('--comment', default=None,
              help=f'Why the account is blocked (max {BLOCKED_USER_COMMENT_MAX_LENGTH} chars).')
@click.pass_context
def blocked_users_add(ctx: click.Context, user_id: int, comment: Optional[str]) -> None:
    """Block a GitHub account from triggering CI runs.

    USER_ID is the *numeric* GitHub account id, not the login: logins can be
    changed and reused, which would silently unblock somebody. Blocking an
    already-blocked account is a 409 (exit code 8).
    """
    if comment is not None and len(comment) > BLOCKED_USER_COMMENT_MAX_LENGTH:
        raise click.BadParameter(
            f'must be {BLOCKED_USER_COMMENT_MAX_LENGTH} characters or less',
            param_hint='--comment')
    send_and_render(ctx, 'POST', '/system/blocked-users',
                    clean_params({'user_id': user_id, 'comment': comment}))


@blocked_users.command('rm')
@click.argument('user_id', type=int)
@click.pass_context
def blocked_users_rm(ctx: click.Context, user_id: int) -> None:
    """Unblock a GitHub account (by numeric id)."""
    send_and_render(ctx, 'DELETE', f'/system/blocked-users/{user_id}')


@admin.group('forbidden-extensions')
def forbidden_extensions() -> None:
    """List, forbid, and re-allow upload file extensions."""


@forbidden_extensions.command('ls')
@click.option('--limit', type=int, default=None, help='Page size (max 100).')
@click.option('--offset', type=int, default=None, help='Pagination offset.')
@click.pass_context
def forbidden_extensions_ls(ctx: click.Context, limit: Optional[int],
                            offset: Optional[int]) -> None:
    """List the file extensions rejected on upload.

    Answers a list of bare strings rather than objects, so table output shows a
    single unnamed column.
    """
    fetch_and_render(ctx, '/system/forbidden-extensions',
                     clean_params({'limit': limit, 'offset': offset}))


@forbidden_extensions.command('add')
@click.argument('extension')
@click.pass_context
def forbidden_extensions_add(ctx: click.Context, extension: str) -> None:
    """Forbid an extension from being uploaded.

    Give it without the leading dot; it is stored lower-cased. Letters and
    digits only, so a glob or pattern can never be smuggled in.
    """
    normalized = extension.lstrip('.').lower()
    if not normalized.isalnum() or not 1 <= len(normalized) <= EXTENSION_MAX_LENGTH:
        raise click.BadParameter(
            f'must be 1 to {EXTENSION_MAX_LENGTH} alphanumeric characters, without a leading dot',
            param_hint='EXTENSION')
    send_and_render(ctx, 'POST', '/system/forbidden-extensions', {'extension': normalized})


@forbidden_extensions.command('rm')
@click.argument('extension')
@click.pass_context
def forbidden_extensions_rm(ctx: click.Context, extension: str) -> None:
    """Allow an extension to be uploaded again."""
    send_and_render(ctx, 'DELETE',
                    f'/system/forbidden-extensions/{extension.lstrip(".").lower()}')
