"""Tests for the CLI's HTTP client, mocking the requests session."""

import unittest
from unittest import mock

import requests  # type: ignore[import-untyped]

from sp_cli.client import ApiClient, ApiError


class FakeResponse:
    """Minimal stand-in for a requests Response."""

    def __init__(self, status_code, json_data=None, raise_json=False):
        """Store the canned status and body."""
        self.status_code = status_code
        self._json = json_data
        self._raise_json = raise_json

    def json(self):
        """Return the canned JSON body or raise like requests does on non-JSON."""
        if self._raise_json:
            raise ValueError('No JSON could be decoded')
        return self._json


class ApiClientTests(unittest.TestCase):
    """Exercise request building and error mapping in the client."""

    @mock.patch('requests.Session.request')
    def test_get_returns_payload_and_builds_url(self, mock_request):
        """A 2xx response is returned and the API prefix is applied."""
        mock_request.return_value = FakeResponse(200, {'data': []})
        client = ApiClient('https://host/api/v1')

        self.assertEqual(client.get('/runs'), {'data': []})
        args, _ = mock_request.call_args
        self.assertEqual(args[0], 'GET')
        self.assertEqual(args[1], 'https://host/api/v1/runs')

    @mock.patch('requests.Session.request')
    def test_204_returns_none(self, mock_request):
        """A 204 (e.g. token revoke) returns None, not a parse error."""
        mock_request.return_value = FakeResponse(204)
        self.assertIsNone(ApiClient('https://host').request('DELETE', '/auth/tokens/current'))

    @mock.patch('requests.Session.request')
    def test_error_codes_map_to_exit_codes(self, mock_request):
        """Each HTTP error maps to its documented exit code."""
        cases = {404: 4, 422: 5, 400: 5, 401: 6, 403: 6, 429: 7}
        # retries=0: this asserts the mapping, not the backoff, and 429 is
        # retryable -- leaving the default on would make it sleep for seconds.
        client = ApiClient('https://host', retries=0)
        for status, expected_exit in cases.items():
            mock_request.return_value = FakeResponse(status, {'code': 'x', 'message': 'm'})
            with self.assertRaises(ApiError) as caught:
                client.get('/runs/9')
            self.assertEqual(caught.exception.exit_code, expected_exit, f'status {status}')

    @mock.patch('requests.Session.request')
    def test_token_is_sent_as_bearer_header(self, mock_request):
        """A configured token is sent as an Authorization header."""
        mock_request.return_value = FakeResponse(200, {})
        ApiClient('https://host', token='secret').get('/runs')
        _, kwargs = mock_request.call_args
        self.assertEqual(kwargs['headers']['Authorization'], 'Bearer secret')

    @mock.patch('requests.Session.request', side_effect=requests.RequestException('boom'))
    def test_connection_failure(self, mock_request):
        """A transport failure maps to a connection_error with exit code 3."""
        with self.assertRaises(ApiError) as caught:
            ApiClient('https://host', retries=0).get('/runs')
        self.assertEqual(caught.exception.code, 'connection_error')
        self.assertEqual(caught.exception.exit_code, 3)

    @mock.patch('requests.Session.request')
    def test_non_json_body_raises_invalid_response(self, mock_request):
        """A non-JSON body raises invalid_response rather than crashing."""
        mock_request.return_value = FakeResponse(500, raise_json=True)
        with self.assertRaises(ApiError) as caught:
            ApiClient('https://host').get('/runs')
        self.assertEqual(caught.exception.code, 'invalid_response')

    @mock.patch('requests.Session.request')
    def test_get_paginated_follows_offset(self, mock_request):
        """Pagination is followed across pages until next_offset is null."""
        mock_request.side_effect = [
            FakeResponse(200, {'data': [1, 2, 3], 'pagination': {'next_offset': 3}}),
            FakeResponse(200, {'data': [4, 5], 'pagination': {'next_offset': None}}),
        ]
        items = ApiClient('https://host').get_paginated('/runs/9/samples')
        self.assertEqual(items, [1, 2, 3, 4, 5])


class RetryTests(unittest.TestCase):
    """Retry behaviour for transient failures.

    Motivated by a live run: `investigate --with-history` makes one call per
    failing sample, and a single 30s read timeout partway through threw away
    every lookup before it.
    """

    def setUp(self):
        """Never actually sleep in tests."""
        patcher = mock.patch('sp_cli.client.time.sleep')
        self.sleep = patcher.start()
        self.addCleanup(patcher.stop)

    @mock.patch('requests.Session.request')
    def test_a_timeout_is_retried_and_can_succeed(self, mock_request):
        """One blip must not lose the whole call."""
        mock_request.side_effect = [
            requests.exceptions.ReadTimeout('timed out'),
            FakeResponse(200, {'run_id': 9}),
        ]
        result = ApiClient('https://host').get('/runs/9')

        self.assertEqual(result, {'run_id': 9})
        self.assertEqual(mock_request.call_count, 2)
        self.sleep.assert_called_once()

    @mock.patch('requests.Session.request')
    def test_retries_are_bounded_and_then_raise(self, mock_request):
        """After the budget is spent the original error still surfaces."""
        mock_request.side_effect = requests.exceptions.ReadTimeout('timed out')
        with self.assertRaises(ApiError) as caught:
            ApiClient('https://host', retries=2).get('/runs/9')

        self.assertEqual(caught.exception.exit_code, 3)
        self.assertEqual(mock_request.call_count, 3)  # 1 attempt + 2 retries

    @mock.patch('requests.Session.request')
    def test_writes_are_never_retried(self, mock_request):
        """POST /runs is not idempotent; a retry could queue the run twice."""
        mock_request.side_effect = requests.exceptions.ReadTimeout('timed out')
        with self.assertRaises(ApiError):
            ApiClient('https://host', retries=5).request('POST', '/runs', json_body={})

        self.assertEqual(mock_request.call_count, 1)

    @mock.patch('requests.Session.request')
    def test_deletes_are_never_retried(self, mock_request):
        """A repeated DELETE turns a success into a confusing 404."""
        mock_request.side_effect = requests.exceptions.ReadTimeout('timed out')
        with self.assertRaises(ApiError):
            ApiClient('https://host', retries=5).delete('/regression-tests/18')

        self.assertEqual(mock_request.call_count, 1)

    @mock.patch('requests.Session.request')
    def test_client_errors_are_not_retried(self, mock_request):
        """A 404 is deterministic -- asking again just wastes a round trip."""
        mock_request.return_value = FakeResponse(404, {'code': 'not_found', 'message': 'no'})
        with self.assertRaises(ApiError):
            ApiClient('https://host', retries=3).get('/runs/9')

        self.assertEqual(mock_request.call_count, 1)

    @mock.patch('requests.Session.request')
    def test_server_errors_and_rate_limits_are_retried(self, mock_request):
        """502/503/504/429 mean 'not now', so they are worth asking again."""
        for status in (429, 502, 503, 504):
            mock_request.reset_mock()
            mock_request.side_effect = [
                FakeResponse(status, {'code': 'x', 'message': 'm'}),
                FakeResponse(200, {'ok': True}),
            ]
            result = ApiClient('https://host').get('/runs')
            self.assertEqual(result, {'ok': True}, f'status {status}')
            self.assertEqual(mock_request.call_count, 2, f'status {status}')

    @mock.patch('requests.Session.request')
    def test_retry_after_header_wins_over_the_computed_backoff(self, mock_request):
        """The server knows better -- but the value is still clamped."""
        response = FakeResponse(429, {'code': 'x', 'message': 'm'})
        response.headers = {'Retry-After': '7'}
        mock_request.side_effect = [response, FakeResponse(200, {})]
        ApiClient('https://host').get('/runs')

        self.assertEqual(self.sleep.call_args.args[0], 7.0)

    @mock.patch('requests.Session.request')
    def test_an_absurd_retry_after_is_clamped(self, mock_request):
        """A header of 3600 must not hang the CLI for an hour."""
        response = FakeResponse(503, {'code': 'x', 'message': 'm'})
        response.headers = {'Retry-After': '3600'}
        mock_request.side_effect = [response, FakeResponse(200, {})]
        ApiClient('https://host').get('/runs')

        self.assertLessEqual(self.sleep.call_args.args[0], 30.0)

    @mock.patch('requests.Session.request')
    def test_a_date_format_retry_after_falls_back_to_backoff(self, mock_request):
        """Retry-After may be an HTTP date; that must not crash the retry."""
        response = FakeResponse(503, {'code': 'x', 'message': 'm'})
        response.headers = {'Retry-After': 'Wed, 21 Oct 2026 07:28:00 GMT'}
        mock_request.side_effect = [response, FakeResponse(200, {})]
        ApiClient('https://host').get('/runs')

        self.assertGreater(self.sleep.call_args.args[0], 0)

    @mock.patch('requests.Session.request')
    def test_retries_can_be_disabled(self, mock_request):
        """--retries 0 restores the old fail-fast behaviour."""
        mock_request.side_effect = requests.exceptions.ReadTimeout('timed out')
        with self.assertRaises(ApiError):
            ApiClient('https://host', retries=0).get('/runs')

        self.assertEqual(mock_request.call_count, 1)
        self.sleep.assert_not_called()
