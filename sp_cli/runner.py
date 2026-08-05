"""Shared helper that fetches from the API and renders the result or error."""

from typing import Any, Dict, Optional

import click

from sp_cli.client import ApiError
from sp_cli.output import render, render_error


def fetch_and_render(ctx: click.Context, path: str, params: Optional[Dict[str, Any]] = None) -> None:
    """
    Fetch a path via the configured client and render it, exiting on error.

    :param ctx: The active Click context (carries the client and output mode).
    :type ctx: click.Context
    :param path: API path below ``/api/v1``.
    :type path: str
    :param params: Optional query-string parameters.
    :type params: Optional[Dict[str, Any]]
    """
    client = ctx.obj['client']
    output = ctx.obj['output']
    try:
        payload = client.get(path, params=params)
    except ApiError as error:
        render_error(error, output)
        raise SystemExit(error.exit_code)
    render(payload, output, ctx.obj.get('color', False))


def send_and_render(ctx: click.Context, method: str, path: str,
                    json_body: Optional[Dict[str, Any]] = None) -> None:
    """
    Send a write request via the configured client and render it, exiting on error.

    The write-side twin of :func:`fetch_and_render`.

    :param ctx: The active Click context (carries the client and output mode).
    :type ctx: click.Context
    :param method: HTTP method (``POST``, ``PATCH``, ``DELETE``).
    :type method: str
    :param path: API path below ``/api/v1``.
    :type path: str
    :param json_body: Optional JSON request body.
    :type json_body: Optional[Dict[str, Any]]
    """
    client = ctx.obj['client']
    output = ctx.obj['output']
    try:
        payload = client.request(method, path, json_body=json_body)
    except ApiError as error:
        render_error(error, output)
        raise SystemExit(error.exit_code)
    render(payload, output, ctx.obj.get('color', False))


def clean_params(params: Dict[str, Any]) -> Dict[str, Any]:
    """
    Drop ``None`` values so unset options are not sent as query parameters.

    :param params: Raw mapping of option names to values.
    :type params: Dict[str, Any]
    :return: The mapping without ``None`` values.
    :rtype: Dict[str, Any]
    """
    return {key: value for key, value in params.items() if value is not None}
