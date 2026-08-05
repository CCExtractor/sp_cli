"""``sp sample`` — list and inspect media samples."""

from typing import Optional

import click

from sp_cli.constants import (PLATFORMS, SAMPLE_CATALOG_STATUSES,
                              SAMPLE_STATUSES)
from sp_cli.runner import clean_params, fetch_and_render


@click.group()
def sample() -> None:
    """List and inspect media samples."""


@sample.command('ls')
@click.option('--name', default=None, help='Substring match on the original sample name.')
@click.option('--tag', default=None, help='Filter by tag.')
@click.option('--extension', default=None, help='Filter by file extension.')
@click.option('--sha256', default=None, help='Filter by exact SHA-256 hash.')
@click.option('--status', type=click.Choice(SAMPLE_CATALOG_STATUSES), default=None,
              help='Catalog visibility, not a test outcome.')
@click.option('--limit', type=int, default=None, help='Page size (max 100).')
@click.option('--offset', type=int, default=None, help='Pagination offset.')
@click.pass_context
def sample_ls(ctx: click.Context, name: Optional[str], tag: Optional[str], extension: Optional[str],
              sha256: Optional[str], status: Optional[str],
              limit: Optional[int], offset: Optional[int]) -> None:
    """List known media samples."""
    params = clean_params({'name': name, 'tag': tag, 'extension': extension, 'sha256': sha256,
                           'status': status, 'limit': limit, 'offset': offset})
    fetch_and_render(ctx, '/samples', params)


@sample.command('show')
@click.argument('sample_id', type=int)
@click.pass_context
def sample_show(ctx: click.Context, sample_id: int) -> None:
    """Show metadata for a single sample."""
    fetch_and_render(ctx, f'/samples/{sample_id}')


@sample.command('details')
@click.argument('sample_id', type=int)
@click.pass_context
def sample_details(ctx: click.Context, sample_id: int) -> None:
    """Show everything known about a sample: upload record, extra files, media info.

    Media info is best-effort -- missing or unparseable XML reports null rather
    than failing the whole response. Unlike the web page this never regenerates
    that XML, because a GET should not write to the sample repository.
    """
    fetch_and_render(ctx, f'/samples/{sample_id}/details')


@sample.command('history')
@click.argument('sample_id', type=int)
@click.option('--platform', type=click.Choice(PLATFORMS), default=None, help='Test platform.')
@click.option('--branch', default=None, help='Filter by branch name.')
@click.option('--status', type=click.Choice(SAMPLE_STATUSES), default=None,
              help='Filter by this sample\'s outcome in each run.')
@click.option('--created-after', 'created_after', default=None,
              help='Only runs first seen at/after this time (ISO 8601).')
@click.option('--created-before', 'created_before', default=None,
              help='Only runs first seen at/before this time (ISO 8601).')
@click.option('--limit', type=int, default=None, help='Page size (max 100).')
@click.option('--offset', type=int, default=None, help='Pagination offset.')
@click.pass_context
def sample_history(ctx: click.Context, sample_id: int, platform: Optional[str],
                   branch: Optional[str], status: Optional[str], created_after: Optional[str],
                   created_before: Optional[str], limit: Optional[int],
                   offset: Optional[int]) -> None:
    """Show this sample's result history across runs.

    Each entry carries a ``failure_signature``, which is what separates a real
    regression from an infra flake that happens to fail the same test.
    """
    params = clean_params({'platform': platform, 'branch': branch, 'status': status,
                           'created_after': created_after, 'created_before': created_before,
                           'limit': limit, 'offset': offset})
    fetch_and_render(ctx, f'/samples/{sample_id}/history', params)
