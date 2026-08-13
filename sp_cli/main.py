"""Entry point and root command group for the ``sp`` CLI."""

from typing import Optional

import click
from click.core import ParameterSource

from sp_cli import __version__, config
from sp_cli.client import ApiClient
from sp_cli.commands.admin import admin
from sp_cli.commands.auth import auth
from sp_cli.commands.category import category
from sp_cli.commands.investigate import investigate
from sp_cli.commands.regression import regression
from sp_cli.commands.run import run
from sp_cli.commands.sample import sample
from sp_cli.commands.system import health, queue

#: The public deployment. There is one canonical Sample Platform, and pointing
#: at it is what almost every caller wants, so `pip install` then
#: `sp investigate <run>` works with nothing else configured. A localhost
#: default was worse than it looked: on macOS, ControlCenter owns port 5000 and
#: answers 403, so the "safe" default produced an auth error from a server that
#: was never the user's. Anyone running their own instance sets SP_BASE_URL, or
#: logs in against it once -- `sp auth login` stores the URL alongside the
#: token, and it is read back below.
DEFAULT_BASE_URL = 'https://sampleplatform.ccextractor.org/api/v1'


@click.group(invoke_without_command=True)
@click.option('--base-url', envvar='SP_BASE_URL', default=DEFAULT_BASE_URL, show_default=True,
              help='API base URL incl. the /api/v1 prefix. Env: SP_BASE_URL.')
@click.option('--token', envvar='SP_API_TOKEN', default=None,
              help='Bearer token sent with each request. Env: SP_API_TOKEN. '
                   'Falls back to the session saved by `sp auth login`.')
@click.option('--output', '-o', type=click.Choice(['json', 'table']), default='json', show_default=True,
              help='Output format.')
@click.option('--timeout', type=int, default=30, show_default=True, help='Per-request timeout (seconds).')
@click.option('--retries', type=int, default=2, show_default=True,
              help='Extra attempts for a failed GET (timeouts, 429, 5xx). 0 disables retrying.')
@click.option('--no-color', is_flag=True, default=False,
              help='Never colorize table output. Also honours the NO_COLOR environment variable.')
@click.version_option(__version__, prog_name='sp')
@click.pass_context
def cli(ctx: click.Context, base_url: str, token: Optional[str], output: str,
        timeout: int, retries: int, no_color: bool) -> None:
    """AI-friendly CLI for the CCExtractor CI / Sample Platform.

    Emits JSON by default so it can be driven by agents and scripts. Point it at
    a running platform with --base-url or the SP_BASE_URL environment variable,
    and authenticate with a token via --token / SP_API_TOKEN (see `sp auth login`).
    """
    # Precedence: --token / SP_API_TOKEN (both bound to `token` by Click) beat
    # the saved session, so an explicit credential always wins.
    if token is None:
        token = config.saved_token()
        if token and config.is_world_readable():
            click.echo(f'Warning: {config.config_path()} is readable by other users; '
                       'run chmod 600 on it.', err=True)

    # Same precedence for the host, one step lower than the flag and env var.
    # Checked by parameter source rather than by value, so passing the default
    # explicitly still beats a saved session -- and so a saved session for a
    # development instance is not overridden by the public default.
    if ctx.get_parameter_source('base_url') == ParameterSource.DEFAULT:
        base_url = config.saved_base_url() or base_url

    ctx.obj = {
        'client': ApiClient(base_url, token=token, timeout=timeout, retries=retries),
        'output': output,
        'base_url': base_url,
        'color': output == 'table' and not no_color,
    }
    if ctx.invoked_subcommand is None:
        from sp_cli.banner import show_welcome
        show_welcome()


cli.add_command(investigate)
cli.add_command(run)
cli.add_command(sample)
cli.add_command(regression)
cli.add_command(category)
cli.add_command(auth)
cli.add_command(admin)
cli.add_command(health)
cli.add_command(queue)
