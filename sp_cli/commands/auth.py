"""``sp auth`` — obtain, list, and revoke API tokens."""

from typing import Optional, Tuple

import click

from sp_cli.client import ApiError
from sp_cli.constants import TOKEN_MAX_DAYS, TOKEN_MIN_DAYS, TOKEN_SCOPES
from sp_cli.output import render, render_error
from sp_cli.runner import clean_params, fetch_and_render


@click.group()
def auth() -> None:
    """Obtain, list, and revoke API tokens."""


@auth.command('login')
@click.option('--email', prompt=True, help='Account email.')
@click.option('--password', prompt=True, hide_input=True, help='Account password (never stored).')
@click.option('--name', 'token_name', default='sp-cli', show_default=True,
              help='Token label; must match ^[a-zA-Z0-9_-]+$.')
@click.option('--days', 'expires_in_days', type=click.IntRange(TOKEN_MIN_DAYS, TOKEN_MAX_DAYS),
              default=TOKEN_MAX_DAYS, show_default=True, help='Token lifetime in days.')
@click.option('--scope', 'scopes', multiple=True, type=click.Choice(TOKEN_SCOPES),
              help='Grant a specific scope; repeatable. Omit for the server default set.')
@click.pass_context
def auth_login(ctx: click.Context, email: str, password: str, token_name: str,
               expires_in_days: int, scopes: Tuple[str, ...]) -> None:
    """Create an API token; store the printed value in SP_API_TOKEN.

    The plaintext token is returned exactly once, at creation. Later `sp auth
    tokens` calls list metadata only, so capture it now or create a new one.
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
    """Revoke the current API token."""
    client = ctx.obj['client']
    output = ctx.obj['output']
    try:
        client.request('DELETE', '/auth/tokens/current')
    except ApiError as error:
        render_error(error, output)
        raise SystemExit(error.exit_code)
    click.echo('Token revoked.')
