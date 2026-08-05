"""``sp auth`` — obtain, list, and revoke API tokens, and manage users."""

from typing import Optional, Tuple

import click

from sp_cli import config
from sp_cli.client import ApiError
from sp_cli.constants import (TOKEN_MAX_DAYS, TOKEN_MIN_DAYS, TOKEN_SCOPES,
                              USER_ROLES)
from sp_cli.output import render, render_error
from sp_cli.runner import clean_params, fetch_and_render, send_and_render


@click.group()
def auth() -> None:
    """Obtain, list, and revoke API tokens, and manage users."""


@auth.command('login')
@click.option('--email', prompt=True, help='Account email.')
@click.option('--password', prompt=True, hide_input=True, help='Account password (never stored).')
@click.option('--name', 'token_name', default='sp-cli', show_default=True,
              help='Token label; must match ^[a-zA-Z0-9_-]+$.')
@click.option('--days', 'expires_in_days', type=click.IntRange(TOKEN_MIN_DAYS, TOKEN_MAX_DAYS),
              default=TOKEN_MAX_DAYS, show_default=True, help='Token lifetime in days.')
@click.option('--scope', 'scopes', multiple=True, type=click.Choice(TOKEN_SCOPES),
              help='Grant a specific scope; repeatable. Omit for the server default set.')
@click.option('--save/--no-save', 'save', default=True, show_default=True,
              help='Save the token to ~/.config/sp/config.json (mode 0600) for later commands.')
@click.pass_context
def auth_login(ctx: click.Context, email: str, password: str, token_name: str,
               expires_in_days: int, scopes: Tuple[str, ...], save: bool) -> None:
    """Create an API token and save it for subsequent commands.

    The plaintext token is returned exactly once, at creation. Later `sp auth
    tokens` calls list metadata only, so it is saved to
    ~/.config/sp/config.json (mode 0600) unless --no-save is given.

    Precedence when a command runs is --token > SP_API_TOKEN > this file, so an
    explicit flag or env var still wins over a saved session.
    """
    client = ctx.obj['client']
    output = ctx.obj['output']
    body = {'email': email, 'password': password,
            'token_name': token_name, 'expires_in_days': expires_in_days}
    if scopes:
        body['scopes'] = list(scopes)
    try:
        result = client.request('POST', '/auth/tokens', json_body=body)
    except ApiError as error:
        render_error(error, output)
        raise SystemExit(error.exit_code)

    token = result.get('token') if isinstance(result, dict) else None
    if save and token:
        path = config.save_token(token, ctx.obj.get('base_url'))
        # To stderr so it never contaminates the JSON on stdout.
        click.echo(f'Token saved to {path} (mode 0600).', err=True)
    render(result, output)


@auth.command('tokens')
@click.option('--limit', type=int, default=None, help='Page size (max 100).')
@click.option('--offset', type=int, default=None, help='Pagination offset.')
@click.pass_context
def auth_tokens(ctx: click.Context, limit: Optional[int], offset: Optional[int]) -> None:
    """List this account's API tokens (metadata only, never the token itself)."""
    params = clean_params({'limit': limit, 'offset': offset})
    fetch_and_render(ctx, '/auth/tokens', params)


@auth.command('revoke')
@click.argument('token_id', type=int)
@click.pass_context
def auth_revoke(ctx: click.Context, token_id: int) -> None:
    """Revoke one token by id, leaving the token in use untouched.

    Use `sp auth tokens` to find the id. To revoke the token you are currently
    authenticating with, use `sp auth logout` instead.
    """
    client = ctx.obj['client']
    output = ctx.obj['output']
    try:
        client.request('DELETE', f'/auth/tokens/{token_id}')
    except ApiError as error:
        render_error(error, output)
        raise SystemExit(error.exit_code)
    click.echo(f'Token {token_id} revoked.')


@auth.command('logout')
@click.pass_context
def auth_logout(ctx: click.Context) -> None:
    """Revoke the current API token and drop the saved session.

    The local file is cleared even if the server call fails, so a token that
    was already revoked or expired cannot leave a stale credential on disk.
    """
    client = ctx.obj['client']
    output = ctx.obj['output']
    try:
        client.request('DELETE', '/auth/tokens/current')
    except ApiError as error:
        config.clear_token()
        render_error(error, output)
        raise SystemExit(error.exit_code)
    cleared = config.clear_token()
    click.echo('Token revoked.' + (' Saved session cleared.' if cleared else ''))


@auth.command('whoami')
@click.pass_context
def auth_whoami(ctx: click.Context) -> None:
    """Show the account and role the current token authenticates as.

    The cheapest way to check that a token is live and carries the scopes a
    command needs, without making a change.
    """
    fetch_and_render(ctx, '/auth/me')


@auth.command('users')
@click.option('--limit', type=int, default=None, help='Page size (max 100).')
@click.option('--offset', type=int, default=None, help='Pagination offset.')
@click.pass_context
def auth_users(ctx: click.Context, limit: Optional[int], offset: Optional[int]) -> None:
    """List platform users, oldest first (admin only).

    Needs the tokens:manage scope as well as the admin role. Password hashes
    and GitHub tokens are never included.
    """
    fetch_and_render(ctx, '/users', clean_params({'limit': limit, 'offset': offset}))


@auth.command('set-role')
@click.argument('user_id', type=int)
@click.argument('role', type=click.Choice(USER_ROLES))
@click.pass_context
def auth_set_role(ctx: click.Context, user_id: int, role: str) -> None:
    """Change a user's role (admin only).

    You cannot change your own role: demoting the last admin here would leave
    nobody able to undo it, so the API answers 403 (exit code 6).
    """
    send_and_render(ctx, 'PATCH', f'/users/{user_id}', {'role': role})
