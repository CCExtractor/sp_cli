"""HTTP client for the CCExtractor CI System API (`/api/v1`)."""

import random
import sys
import time
from typing import Any, Dict, List, Optional

import requests  # type: ignore[import-untyped]

#: Statuses where the server said "not now" rather than "no". Retrying these is
#: the difference between riding out a blip and losing a whole investigation:
#: ``investigate --with-history`` makes one call per failing sample, so a single
#: timeout 40 lookups in would otherwise throw away everything before it.
_RETRY_STATUSES = frozenset({429, 502, 503, 504})

#: Only GET is retried. POST /runs is not idempotent -- a retry that raced a
#: slow-but-successful first attempt would queue the run twice -- and a repeated
#: DELETE turns a success into a confusing 404.
_RETRYABLE_METHODS = frozenset({'GET'})

#: Base for exponential backoff, in seconds: 0.5, 1.0, 2.0 ...
_BACKOFF_BASE = 0.5

#: Ceiling on a single sleep, so a large Retry-After cannot hang the CLI.
_BACKOFF_MAX = 30.0


class ApiError(Exception):
    """Raised when an API request fails, carrying the structured error envelope."""

    def __init__(self, code: str, message: str, status: Optional[int] = None,
                 details: Optional[Dict[str, Any]] = None) -> None:
        """
        Build an API error.

        :param code: Stable machine-readable error code (e.g. ``not_found``).
        :type code: str
        :param message: Human-readable explanation.
        :type message: str
        :param status: HTTP status code, if the failure was an HTTP response.
        :type status: Optional[int]
        :param details: Optional structured context echoed from the API.
        :type details: Optional[Dict[str, Any]]
        """
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.details = details

    @property
    def exit_code(self) -> int:
        """
        Map the error to a process exit code so callers can branch on it.

        :return: 3 connection · 4 not-found · 5 validation · 6 auth · 7 rate-limited ·
                 8 conflict · 1 other.
        :rtype: int
        """
        if self.code == 'connection_error':
            return 3
        if self.status == 404:
            return 4
        if self.status in (400, 422):
            return 5
        if self.status in (401, 403):
            return 6
        if self.status == 429:
            return 7
        # A refused delete (test/category still referenced) or a duplicate name.
        # Distinct from validation: the body was fine, the world disagreed.
        if self.status == 409:
            return 8
        return 1


class ApiClient:
    """Minimal client over the JSON API. Sends a bearer token when configured."""

    def __init__(self, base_url: str, token: Optional[str] = None, timeout: int = 30,
                 retries: int = 2) -> None:
        """
        Configure the client.

        :param base_url: Root URL of the platform (without the ``/api/v1`` prefix).
        :type base_url: str
        :param token: Optional opaque bearer token sent on every request.
        :type token: Optional[str]
        :param timeout: Per-request timeout in seconds.
        :type timeout: int
        :param retries: Extra attempts for a failed GET. 0 disables retrying.
        :type retries: int
        """
        self.base_url = base_url.rstrip('/')
        self.token = token
        self.timeout = timeout
        self.retries = max(0, retries)
        self.session = requests.Session()

    def _headers(self) -> Dict[str, str]:
        """
        Build request headers, including the bearer token when set.

        :return: Header mapping.
        :rtype: Dict[str, str]
        """
        headers = {'Accept': 'application/json'}
        if self.token:
            headers['Authorization'] = f'Bearer {self.token}'
        return headers

    def request(self, method: str, path: str, params: Optional[Dict[str, Any]] = None,
                json_body: Optional[Dict[str, Any]] = None) -> Any:
        """
        Perform a request against an API path and return the decoded JSON body.

        :param method: HTTP method (``GET``, ``POST``, ``DELETE`` …).
        :type method: str
        :param path: API path below ``/api/v1`` (e.g. ``/runs``).
        :type path: str
        :param params: Optional query-string parameters.
        :type params: Optional[Dict[str, Any]]
        :param json_body: Optional JSON request body.
        :type json_body: Optional[Dict[str, Any]]
        :raises ApiError: on connection failure, a non-JSON body, or an HTTP error.
        :return: The decoded JSON response body (or ``None`` for ``204``).
        :rtype: Any
        """
        url = f"{self.base_url}{path}"
        attempts = 1
        if method.upper() in _RETRYABLE_METHODS:
            attempts += self.retries

        for attempt in range(1, attempts + 1):
            last = attempt == attempts
            try:
                response = self.session.request(method, url, params=params, json=json_body,
                                                headers=self._headers(), timeout=self.timeout)
            except requests.RequestException as exc:
                if last:
                    raise ApiError('connection_error', f'Could not reach {url}: {exc}')
                self._wait(attempt, None, f'{type(exc).__name__} on {method} {path}')
                continue

            if response.status_code in _RETRY_STATUSES and not last:
                self._wait(attempt, response, f'HTTP {response.status_code} on {method} {path}')
                continue
            break

        if response.status_code == 204:
            return None

        try:
            payload = response.json()
        except ValueError:
            raise ApiError('invalid_response',
                           f'Expected JSON but got HTTP {response.status_code}', response.status_code)

        if response.status_code >= 400:
            error = payload if isinstance(payload, dict) else {}
            raise ApiError(
                error.get('code', 'http_error'),
                error.get('message', f'Request failed with HTTP {response.status_code}'),
                response.status_code,
                error.get('details'),
            )

        return payload

    def _wait(self, attempt: int, response: Optional[Any], reason: str) -> None:
        """
        Sleep before the next attempt, and say so on stderr.

        Backoff is exponential with jitter, so a fleet of agents retrying the
        same blip does not resynchronise into a second thundering herd. A
        ``Retry-After`` header wins over the computed delay, because the server
        knows better than we do -- but it is still clamped, so a header of 3600
        cannot silently hang the CLI for an hour.

        :param attempt: Which attempt just failed, counting from 1.
        :type attempt: int
        :param response: The failed response, when there was one.
        :type response: Optional[Any]
        :param reason: Short description of what failed, for the notice.
        :type reason: str
        """
        delay = min(_BACKOFF_BASE * (2 ** (attempt - 1)), _BACKOFF_MAX)
        delay += random.uniform(0, delay / 2)

        if response is not None:
            headers = getattr(response, 'headers', None) or {}
            header = headers.get('Retry-After')
            if header:
                try:
                    delay = min(float(header), _BACKOFF_MAX)
                except (TypeError, ValueError):
                    pass  # A date-format Retry-After; the computed backoff stands.

        # stderr, so a retry notice can never contaminate JSON on stdout.
        print(f'{reason}; retrying in {delay:.1f}s', file=sys.stderr)
        time.sleep(delay)

    def get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Any:
        """
        Perform a GET and return the decoded body.

        :param path: API path below ``/api/v1``.
        :type path: str
        :param params: Optional query-string parameters.
        :type params: Optional[Dict[str, Any]]
        :return: The decoded JSON body.
        :rtype: Any
        """
        return self.request('GET', path, params=params)

    def post(self, path: str, json_body: Optional[Dict[str, Any]] = None) -> Any:
        """
        Perform a POST and return the decoded body.

        :param path: API path below ``/api/v1``.
        :type path: str
        :param json_body: Optional JSON request body.
        :type json_body: Optional[Dict[str, Any]]
        :return: The decoded JSON body.
        :rtype: Any
        """
        return self.request('POST', path, json_body=json_body)

    def patch(self, path: str, json_body: Optional[Dict[str, Any]] = None) -> Any:
        """
        Perform a PATCH and return the decoded body.

        :param path: API path below ``/api/v1``.
        :type path: str
        :param json_body: Optional JSON request body.
        :type json_body: Optional[Dict[str, Any]]
        :return: The decoded JSON body.
        :rtype: Any
        """
        return self.request('PATCH', path, json_body=json_body)

    def delete(self, path: str) -> Any:
        """
        Perform a DELETE and return the decoded body.

        :param path: API path below ``/api/v1``.
        :type path: str
        :return: The decoded JSON body (or ``None`` for ``204``).
        :rtype: Any
        """
        return self.request('DELETE', path)

    def get_paginated(self, path: str, params: Optional[Dict[str, Any]] = None,
                      max_items: int = 1000) -> List[Any]:
        """
        Follow offset pagination and return the combined ``data`` list.

        :param path: API path below ``/api/v1``.
        :type path: str
        :param params: Optional query-string parameters (``limit``/``offset`` are managed).
        :type params: Optional[Dict[str, Any]]
        :param max_items: Safety cap on total items collected.
        :type max_items: int
        :return: All items across pages.
        :rtype: List[Any]
        """
        merged = dict(params or {})
        merged.setdefault('limit', 100)
        offset = 0
        items: List[Any] = []
        while True:
            merged['offset'] = offset
            payload = self.get(path, params=merged)
            data = payload.get('data', []) if isinstance(payload, dict) else []
            items.extend(data)
            pagination = payload.get('pagination', {}) if isinstance(payload, dict) else {}
            next_offset = pagination.get('next_offset')
            if not data or next_offset is None or len(items) >= max_items:
                break
            offset = next_offset
        return items

    def get_cursor_paginated(self, path: str, params: Optional[Dict[str, Any]] = None,
                             max_items: int = 5000) -> List[Any]:
        """
        Follow cursor pagination and return the combined ``data`` list.

        Distinct from :meth:`get_paginated` because the API refuses to mix the
        two schemes: sending ``offset`` to a cursor-paginated endpoint is a 400.
        The cursor is opaque here -- it is echoed back exactly as received.

        :param path: API path below ``/api/v1``.
        :type path: str
        :param params: Optional query-string parameters (``cursor`` is managed).
        :type params: Optional[Dict[str, Any]]
        :param max_items: Safety cap on total items collected.
        :type max_items: int
        :return: All items across pages.
        :rtype: List[Any]
        """
        base = dict(params or {})
        cursor = base.pop('cursor', None)
        items: List[Any] = []
        while True:
            # A fresh mapping per page: reusing one would alias into every call.
            page_params = dict(base)
            if cursor is not None:
                page_params['cursor'] = cursor
            payload = self.get(path, params=page_params)
            data = payload.get('data', []) if isinstance(payload, dict) else []
            items.extend(data)
            pagination = payload.get('pagination', {}) if isinstance(payload, dict) else {}
            cursor = pagination.get('next_cursor')
            if not data or cursor is None or len(items) >= max_items:
                break
        return items
