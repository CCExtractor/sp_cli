"""``sp health`` and ``sp queue`` — system status commands."""

from typing import Optional

import click

from sp_cli.constants import PLATFORMS, QUEUE_STATUSES
from sp_cli.runner import clean_params, fetch_and_render


@click.command('health')
@click.pass_context
def health(ctx: click.Context) -> None:
    """Show CI system health and dependency status."""
    fetch_and_render(ctx, '/system/health')


@click.command('queue')
@click.option('--platform', type=click.Choice(PLATFORMS), default=None, help='Test platform.')
@click.option('--status', type=click.Choice(QUEUE_STATUSES), default=None,
              help='Restrict to one side of the queue.')
@click.option('--limit', type=int, default=None, help='Page size (max 100).')
@click.option('--offset', type=int, default=None, help='Pagination offset.')
@click.pass_context
def queue(ctx: click.Context, platform: Optional[str], status: Optional[str],
          limit: Optional[int], offset: Optional[int]) -> None:
    """Show queue depth and currently running jobs.

    Completed and canceled runs are excluded. The per-item ``position`` field
    is only populated when ``--status queued`` is passed; otherwise it is null.
    """
    params = clean_params({'platform': platform, 'status': status,
                           'limit': limit, 'offset': offset})
    fetch_and_render(ctx, '/system/queue', params)
