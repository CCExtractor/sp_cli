"""Render API responses to the terminal as JSON (default) or a simple table."""

import json
import os
import sys
from typing import Any, Dict, List

import click

from sp_cli.client import ApiError

#: Value types rendered as plain table columns; nested structures are skipped.
_SCALAR = (str, int, float, bool, type(None))

#: Columns worth colorizing, and the colour each value gets. Severity reads
#: left to right: red is a crash, yellow a wrong result, cyan a diff to review.
_CODE_COLORS = {
    'SEGFAULT': 'red',
    'ABORT': 'red',
    'TIMEOUT': 'yellow',
    'EXIT_CODE_MISMATCH': 'yellow',
    'MISSING_OUTPUT': 'magenta',
    'OUTPUT_DIFF': 'cyan',
    'PASS': 'green',
    # `investigate --with-history` verdicts, shown in the same table.
    'NEW_REGRESSION': 'red',
    'STILL_FAILING': 'yellow',
    'NEVER_PASSED': 'magenta',
    'FLAKY': 'cyan',
    'NO_HISTORY': 'white',
    'UNKNOWN': 'white',
}

#: Only these columns are ever colorized; everything else stays plain.
_COLORIZED_COLUMNS = ('code', 'verdict')


def render(payload: Any, output: str, color: bool = False) -> None:
    """
    Render a successful API payload in the requested format.

    Handles the API's three shapes: a list wrapper (``{data, pagination}``), a
    flat single object (run/summary/health), and bare values.

    :param payload: The decoded JSON body returned by the API.
    :type payload: Any
    :param output: Either ``json`` or ``table``.
    :type output: str
    :param color: Whether the caller wants colour; still suppressed when stdout
        is not a TTY or ``NO_COLOR`` is set.
    :type color: bool
    """
    if output == 'json':
        click.echo(json.dumps(payload, indent=2))
        return

    use_color = _color_enabled(color)
    if isinstance(payload, dict) and isinstance(payload.get('data'), list):
        _print_rows(payload['data'], use_color)
        footer = _footer(payload)
        if footer:
            click.echo(f"\n{footer}")
    elif isinstance(payload, dict):
        _print_kv(payload)
    else:
        click.echo(json.dumps(payload, indent=2))


def _color_enabled(requested: bool) -> bool:
    """
    Decide whether colour may actually be emitted.

    Colour is decoration, so it is dropped whenever the output is not a live
    terminal -- piping into ``jq`` or a file must never receive escape codes.

    :param requested: Whether the caller asked for colour.
    :type requested: bool
    :return: ``True`` only when colour is both wanted and safe.
    :rtype: bool
    """
    if not requested:
        return False
    if os.environ.get('NO_COLOR'):
        return False
    return sys.stdout.isatty()


def render_error(error: ApiError, output: str) -> None:
    """
    Render an API error as a JSON envelope on stderr, regardless of output mode.

    :param error: The error to render.
    :type error: ApiError
    :param output: The selected output mode (unused; kept for symmetry).
    :type output: str
    """
    envelope: Dict[str, Any] = {'error': {'code': error.code, 'message': error.message}}
    if error.status is not None:
        envelope['error']['status'] = error.status
    if error.details:
        envelope['error']['details'] = error.details
    click.echo(json.dumps(envelope, indent=2), err=True)


def _footer(payload: Dict[str, Any]) -> str:
    """
    Build a one-line footer from a ``summary`` or ``pagination`` block.

    :param payload: The full response payload.
    :type payload: Dict[str, Any]
    :return: A footer string (possibly empty).
    :rtype: str
    """
    summary = payload.get('summary')
    if isinstance(summary, dict):
        return ' · '.join(f"{k}: {v}" for k, v in summary.items())
    pagination = payload.get('pagination')
    if isinstance(pagination, dict):
        parts = []
        if pagination.get('total') is not None:
            parts.append(f"{pagination['total']} total")
        if pagination.get('next_offset') is not None:
            parts.append(f"more at offset {pagination['next_offset']}")
        if pagination.get('next_cursor') is not None:
            parts.append(f"more at cursor {pagination['next_cursor']}")
        return ' · '.join(parts)
    return ''


def _print_rows(rows: List[Any], color: bool = False) -> None:
    """
    Print a list of flat dicts as an aligned table of their scalar fields.

    :param rows: The list of row dicts to render.
    :type rows: List[Any]
    :param color: Whether to colorize the classification columns.
    :type color: bool
    """
    if not rows:
        click.echo('(no results)')
        return
    if not all(isinstance(row, dict) for row in rows):
        click.echo(json.dumps(rows, indent=2))
        return

    columns: List[str] = []
    for row in rows:
        for key, value in row.items():
            if key not in columns and isinstance(value, _SCALAR):
                columns.append(key)

    widths = {col: len(col) for col in columns}
    for row in rows:
        for col in columns:
            widths[col] = max(widths[col], len(_cell(row.get(col))))

    click.echo('  '.join(col.ljust(widths[col]) for col in columns))
    click.echo('  '.join('-' * widths[col] for col in columns))
    for row in rows:
        # Padded first, then styled: escape codes have no display width, so
        # colorizing before ljust would push every later column out of line.
        click.echo('  '.join(
            _paint(_cell(row.get(col)).ljust(widths[col]), col, row.get(col), color)
            for col in columns))


def _paint(padded: str, column: str, value: Any, color: bool) -> str:
    """
    Apply the classification colour to an already-padded cell.

    :param padded: The cell text, already widened to the column width.
    :type padded: str
    :param column: The column name the cell belongs to.
    :type column: str
    :param value: The raw cell value, used to pick the colour.
    :type value: Any
    :param color: Whether colour is enabled at all.
    :type color: bool
    :return: The cell, styled or untouched.
    :rtype: str
    """
    if not color or column not in _COLORIZED_COLUMNS:
        return padded
    fg = _CODE_COLORS.get(str(value))
    return click.style(padded, fg=fg) if fg else padded


def _print_kv(record: Dict[str, Any]) -> None:
    """
    Print a single record as ``key: value`` lines, JSON-encoding nested values.

    :param record: The record to render.
    :type record: Dict[str, Any]
    """
    width = max((len(key) for key in record), default=0)
    for key, value in record.items():
        rendered = _cell(value) if isinstance(value, _SCALAR) else json.dumps(value)
        click.echo(f"{key.ljust(width)} : {rendered}")


def _cell(value: Any) -> str:
    """
    Format a scalar cell value for table display.

    :param value: The value to format.
    :type value: Any
    :return: A string representation (empty string for ``None``).
    :rtype: str
    """
    if value is None:
        return ''
    if isinstance(value, bool):
        return 'true' if value else 'false'
    if isinstance(value, (list, tuple)):
        return ', '.join(str(item) for item in value)
    return str(value)
