"""Tests for the sp CLI command surface, mocking the API client."""

import json
import unittest
from unittest import mock

from click.testing import CliRunner

from sp_cli.client import ApiError
from sp_cli.main import cli

RUNS_PAGE = {
    'data': [{'run_id': 9299, 'status': 'fail', 'platform': 'windows', 'commit_sha': 'e6cd34e'}],
    'pagination': {'limit': 50, 'offset': 0, 'total': 1, 'next_offset': None},
}

# A run's results: a segfault, an exit mismatch, a missing output, and a pass.
RUN_SAMPLES = [
    {'regression_test_id': 18, 'sample_name': 'dvb', 'categories': ['DVB'],
     'status': 'fail', 'exit_code': -1073741819, 'expected_rc': 0, 'outputs': []},
    {'regression_test_id': 137, 'sample_name': 'cea708', 'categories': ['CEA-708'],
     'status': 'fail', 'exit_code': 10, 'expected_rc': 0, 'outputs': []},
    {'regression_test_id': 7, 'sample_name': 'broken', 'categories': ['Broken'],
     'status': 'missing_output', 'exit_code': 0, 'expected_rc': 0, 'outputs': []},
    {'regression_test_id': 1, 'sample_name': 'ok', 'categories': ['General'],
     'status': 'pass', 'exit_code': 0, 'expected_rc': 0, 'outputs': []},
]

# Two failures on distinct media samples, which is what history lookups key on.
SAMPLES_WITH_IDS = [
    {'regression_test_id': 18, 'sample_id': 42, 'sample_name': 'dvb', 'categories': ['DVB'],
     'status': 'fail', 'exit_code': 10, 'expected_rc': 0, 'outputs': []},
    {'regression_test_id': 137, 'sample_id': 43, 'sample_name': 'cea708',
     'categories': ['CEA-708'], 'status': 'missing_output', 'exit_code': 0,
     'expected_rc': 0, 'outputs': []},
]


class CliCommandTests(unittest.TestCase):
    """Exercise the CLI commands with a mocked client."""

    def setUp(self):
        """Create a runner for each test."""
        self.runner = CliRunner()

    @mock.patch('sp_cli.client.ApiClient.get')
    def test_run_ls_calls_runs_with_filters(self, mock_get):
        """`run ls` hits /runs and forwards set filters only."""
        mock_get.return_value = RUNS_PAGE
        result = self.runner.invoke(cli, ['run', 'ls', '--platform', 'windows'])

        self.assertEqual(result.exit_code, 0)
        mock_get.assert_called_once_with('/runs', params={'platform': 'windows'})

    @mock.patch('sp_cli.client.ApiClient.get')
    def test_run_show(self, mock_get):
        """`run show <id>` targets the run detail path."""
        mock_get.return_value = {'run_id': 9299, 'status': 'fail'}
        result = self.runner.invoke(cli, ['run', 'show', '9299'])

        self.assertEqual(result.exit_code, 0)
        mock_get.assert_called_once_with('/runs/9299', params=None)

    @mock.patch('sp_cli.client.ApiClient.get_paginated')
    def test_run_failures_classifies(self, mock_paginated):
        """`run failures` keeps only failures and labels each with a code."""
        mock_paginated.return_value = RUN_SAMPLES
        result = self.runner.invoke(cli, ['run', 'failures', '9299'])

        self.assertEqual(result.exit_code, 0)
        mock_paginated.assert_called_once_with('/runs/9299/samples')
        data = json.loads(result.output)
        codes = {row['regression_test_id']: row['code'] for row in data['data']}
        self.assertEqual(codes, {18: 'SEGFAULT', 137: 'EXIT_CODE_MISMATCH', 7: 'MISSING_OUTPUT'})
        self.assertEqual(data['summary'], {'failures': 3, 'of_total': 4})

    @mock.patch('sp_cli.client.ApiClient.get_paginated')
    def test_run_failures_table_output(self, mock_paginated):
        """Table mode renders the classification columns."""
        mock_paginated.return_value = RUN_SAMPLES
        result = self.runner.invoke(cli, ['-o', 'table', 'run', 'failures', '9299'])

        self.assertEqual(result.exit_code, 0)
        self.assertIn('SEGFAULT', result.output)
        self.assertIn('code', result.output)

    @mock.patch('sp_cli.client.ApiClient.get')
    def test_sample_ls(self, mock_get):
        """`sample ls` hits /samples."""
        mock_get.return_value = {'data': [], 'pagination': {'total': 0, 'next_offset': None}}
        result = self.runner.invoke(cli, ['sample', 'ls', '--tag', 'teletext'])

        self.assertEqual(result.exit_code, 0)
        mock_get.assert_called_once_with('/samples', params={'tag': 'teletext'})

    @mock.patch('sp_cli.client.ApiClient.get')
    def test_health(self, mock_get):
        """`sp health` hits /system/health."""
        mock_get.return_value = {'status': 'ok', 'dependencies': []}
        result = self.runner.invoke(cli, ['health'])

        self.assertEqual(result.exit_code, 0)
        mock_get.assert_called_once_with('/system/health', params=None)

    @mock.patch('sp_cli.client.ApiClient.get')
    def test_not_found_maps_to_exit_code_and_stderr(self, mock_get):
        """A not-found error exits 4 with a JSON envelope on stderr."""
        mock_get.side_effect = ApiError('not_found', 'Run 9 not found', 404)
        result = self.runner.invoke(cli, ['run', 'show', '9'])

        self.assertEqual(result.exit_code, 4)
        self.assertEqual(json.loads(result.stderr)['error']['code'], 'not_found')

    @mock.patch('sp_cli.client.ApiClient.get')
    def test_run_result(self, mock_get):
        """`run result <run> <sample>` targets the result-detail path."""
        mock_get.return_value = {'regression_test_id': 137, 'status': 'fail'}
        result = self.runner.invoke(cli, ['run', 'result', '9299', '5'])

        self.assertEqual(result.exit_code, 0)
        mock_get.assert_called_once_with('/runs/9299/samples/5', params=None)

    @mock.patch('sp_cli.client.ApiClient.get')
    def test_run_diff_auto_resolves_hidden_ids(self, mock_get):
        """`run diff` resolves the media sample id + regression/output ids from detail."""
        mock_get.side_effect = [
            {'regression_test_id': 137, 'sample_id': 42,
             'outputs': [{'output_id': 2, 'status': 'fail'}]},
            {'status': 'different', 'hunks': []},
        ]
        result = self.runner.invoke(cli, ['run', 'diff', '9299', '5'])

        self.assertEqual(result.exit_code, 0)
        self.assertEqual(mock_get.call_count, 2)
        args, kwargs = mock_get.call_args
        self.assertEqual(args[0], '/runs/9299/samples/42/regression-tests/137/outputs/2/diff')
        self.assertEqual(kwargs['params'], {})

    @mock.patch('sp_cli.client.ApiClient.get')
    def test_run_diff_with_explicit_ids_uses_media_sample_from_detail(self, mock_get):
        """Explicit --regression/--output still fetch detail for the media sample id."""
        mock_get.side_effect = [
            {'regression_test_id': 137, 'sample_id': 42, 'outputs': []},
            {'status': 'different', 'hunks': []},
        ]
        result = self.runner.invoke(cli, ['run', 'diff', '9299', '5', '--regression', '137', '--output', '2'])

        self.assertEqual(result.exit_code, 0)
        self.assertEqual(mock_get.call_count, 2)
        args, kwargs = mock_get.call_args
        self.assertEqual(args[0], '/runs/9299/samples/42/regression-tests/137/outputs/2/diff')
        self.assertEqual(kwargs['params'], {})

    @mock.patch('sp_cli.client.ApiClient.request')
    @mock.patch('sp_cli.client.ApiClient.get')
    def test_run_approve_baseline_resolves_media_sample_and_posts(self, mock_get, mock_request):
        """`run approve-baseline` POSTs to the media-sample path resolved from detail."""
        mock_get.return_value = {'regression_test_id': 137, 'sample_id': 42, 'outputs': []}
        mock_request.return_value = {'status': 'approved', 'run_id': 9299, 'sample_id': 42,
                                     'regression_id': 137, 'output_id': 2}
        result = self.runner.invoke(cli, ['run', 'approve-baseline', '9299', '5',
                                          '--regression', '137', '--output', '2', '--remove-variants'])

        self.assertEqual(result.exit_code, 0)
        mock_get.assert_called_once_with('/runs/9299/samples/5')
        mock_request.assert_called_once_with(
            'POST', '/runs/9299/samples/42/baseline-approval',
            json_body={'regression_id': 137, 'output_id': 2, 'remove_variants': True})

    @mock.patch('sp_cli.client.ApiClient.get')
    def test_run_approve_baseline_requires_regression_and_output(self, mock_get):
        """Approving a baseline refuses to run without the explicit target ids."""
        result = self.runner.invoke(cli, ['run', 'approve-baseline', '9299', '5'])

        self.assertNotEqual(result.exit_code, 0)
        mock_get.assert_not_called()

    @mock.patch('sp_cli.client.ApiClient.get_paginated')
    @mock.patch('sp_cli.client.ApiClient.get')
    def test_investigate_combines_run_summary_and_failures(self, mock_get, mock_paginated):
        """`investigate` merges run detail, summary, and classified failures."""
        mock_get.side_effect = [
            {'run_id': 9299, 'pr_number': 2264, 'platform': 'windows', 'status': 'fail'},
            {'run_id': 9299, 'total_samples': 4, 'pass_count': 1, 'fail_count': 3},
        ]
        mock_paginated.return_value = RUN_SAMPLES
        result = self.runner.invoke(cli, ['investigate', '9299'])

        self.assertEqual(result.exit_code, 0)
        report = json.loads(result.output)
        self.assertEqual(report['run']['pr_number'], 2264)
        self.assertEqual(report['summary']['fail_count'], 3)
        self.assertEqual(report['by_code'],
                         {'SEGFAULT': 1, 'EXIT_CODE_MISMATCH': 1, 'MISSING_OUTPUT': 1})
        self.assertEqual(len(report['failures']), 3)


class InvestigateHistoryTests(unittest.TestCase):
    """`investigate --with-history` separates new regressions from known gaps."""

    def setUp(self):
        """Create a runner for each test."""
        self.runner = CliRunner()

    @staticmethod
    def _history_for(regression_test_id):
        """
        Build a plausible history for one regression test.

        :param regression_test_id: The regression test the entries belong to.
        :type regression_test_id: int
        :return: History entries, newest first.
        :rtype: list
        """
        return [
            {'run_id': 9299, 'regression_test_id': regression_test_id, 'status': 'fail',
             'failure_signature': 'exit_code_mismatch:rc:10'},
            {'run_id': 9298, 'regression_test_id': regression_test_id, 'status': 'pass',
             'failure_signature': None},
        ]

    def _invoke(self, mock_get, mock_paginated, args, histories):
        """
        Run `investigate` with the run/summary/samples calls and histories stubbed.

        :param mock_get: The patched ``ApiClient.get``.
        :param mock_paginated: The patched ``ApiClient.get_paginated``.
        :param args: CLI arguments.
        :type args: list
        :param histories: Per-sample history responses, keyed by sample id.
        :type histories: dict
        :return: The Click result.
        """
        mock_get.side_effect = [
            {'run_id': 9299, 'pr_number': 2264, 'platform': 'windows', 'status': 'fail'},
            {'run_id': 9299, 'total_samples': 2, 'pass_count': 0, 'fail_count': 2},
        ]

        def paginated(path, params=None, max_items=1000):
            if path.endswith('/samples'):
                return SAMPLES_WITH_IDS
            sample_id = int(path.split('/samples/')[1].split('/')[0])
            return histories.get(sample_id, [])

        mock_paginated.side_effect = paginated
        return self.runner.invoke(cli, args)

    @mock.patch('sp_cli.client.ApiClient.get_paginated')
    @mock.patch('sp_cli.client.ApiClient.get')
    def test_history_labels_regression_versus_never_passing(self, mock_get, mock_paginated):
        """A sample that passed last run is a regression; one that never passed is not."""
        result = self._invoke(mock_get, mock_paginated,
                              ['investigate', '9299', '--with-history'],
                              {42: self._history_for(18),
                               43: [{'run_id': 9298, 'regression_test_id': 137,
                                     'status': 'fail', 'failure_signature': 'missing_output'}]})

        self.assertEqual(result.exit_code, 0)
        report = json.loads(result.output)
        verdicts = {f['regression_test_id']: f['history']['verdict'] for f in report['failures']}
        self.assertEqual(verdicts, {18: 'NEW_REGRESSION', 137: 'NEVER_PASSED'})
        self.assertEqual(report['by_verdict'], {'NEW_REGRESSION': 1, 'NEVER_PASSED': 1})

    @mock.patch('sp_cli.client.ApiClient.get_paginated')
    @mock.patch('sp_cli.client.ApiClient.get')
    def test_history_is_filtered_to_the_run_platform(self, mock_get, mock_paginated):
        """A Windows failure must not be judged against Linux history."""
        self._invoke(mock_get, mock_paginated, ['investigate', '9299', '--with-history'],
                     {42: self._history_for(18), 43: self._history_for(137)})

        history_calls = [c for c in mock_paginated.call_args_list
                         if '/history' in c.args[0]]
        self.assertEqual(len(history_calls), 2)
        for call in history_calls:
            self.assertEqual(call.kwargs['params']['platform'], 'windows')

    @mock.patch('sp_cli.client.ApiClient.get_paginated')
    @mock.patch('sp_cli.client.ApiClient.get')
    def test_history_depth_implies_the_flag_and_asks_for_a_full_page(self, mock_get, mock_paginated):
        """--history-depth alone turns history on; the page size is always the maximum.

        Sizing the page to depth + 1 looked right but delivered a fraction of
        it: the endpoint returns entries for every regression test on the
        sample and slices only afterwards, so the window has to be filtered
        client-side out of as large a page as the API will give.
        """
        from sp_cli.constants import MAX_PAGE_LIMIT

        result = self._invoke(mock_get, mock_paginated,
                              ['investigate', '9299', '--history-depth', '5'],
                              {42: self._history_for(18), 43: self._history_for(137)})

        self.assertEqual(result.exit_code, 0)
        self.assertIn('by_verdict', json.loads(result.output))
        history_call = next(c for c in mock_paginated.call_args_list if '/history' in c.args[0])
        self.assertEqual(history_call.kwargs['params']['limit'], MAX_PAGE_LIMIT)
        self.assertEqual(history_call.kwargs['max_items'], MAX_PAGE_LIMIT)

    @mock.patch('sp_cli.client.ApiClient.get_paginated')
    @mock.patch('sp_cli.client.ApiClient.get')
    def test_without_the_flag_no_history_is_fetched(self, mock_get, mock_paginated):
        """The default stays one call per run — history is opt-in."""
        result = self._invoke(mock_get, mock_paginated, ['investigate', '9299'], {})

        self.assertEqual(result.exit_code, 0)
        report = json.loads(result.output)
        self.assertNotIn('by_verdict', report)
        self.assertNotIn('history', report['failures'][0])
        self.assertFalse([c for c in mock_paginated.call_args_list if '/history' in c.args[0]])

    @mock.patch('sp_cli.client.ApiClient.get_paginated')
    @mock.patch('sp_cli.client.ApiClient.get')
    def test_one_lookup_per_sample_even_when_tests_share_it(self, mock_get, mock_paginated):
        """Two regression tests on one sample must not cost two history calls."""
        mock_get.side_effect = [
            {'run_id': 9299, 'platform': 'windows', 'status': 'fail'},
            {'run_id': 9299, 'total_samples': 2, 'pass_count': 0, 'fail_count': 2},
        ]
        shared = [dict(s, sample_id=42) for s in SAMPLES_WITH_IDS]

        def paginated(path, params=None, max_items=1000):
            if path.endswith('/samples'):
                return shared
            return self._history_for(18) + self._history_for(137)

        mock_paginated.side_effect = paginated
        result = self.runner.invoke(cli, ['investigate', '9299', '--with-history'])

        self.assertEqual(result.exit_code, 0)
        history_calls = [c for c in mock_paginated.call_args_list if '/history' in c.args[0]]
        self.assertEqual(len(history_calls), 1)

    @mock.patch('sp_cli.client.ApiClient.get_paginated')
    @mock.patch('sp_cli.client.ApiClient.get')
    def test_missing_sample_id_yields_unknown_not_a_crash(self, mock_get, mock_paginated):
        """A result with no sample id still gets a verdict block."""
        mock_get.side_effect = [
            {'run_id': 9299, 'platform': 'windows', 'status': 'fail'},
            {'run_id': 9299, 'total_samples': 1, 'pass_count': 0, 'fail_count': 1},
        ]
        mock_paginated.return_value = [
            {'regression_test_id': 18, 'sample_id': None, 'sample_name': 'orphan',
             'categories': [], 'status': 'fail', 'exit_code': 1, 'expected_rc': 0, 'outputs': []},
        ]
        result = self.runner.invoke(cli, ['investigate', '9299', '--with-history'])

        self.assertEqual(result.exit_code, 0)
        report = json.loads(result.output)
        self.assertEqual(report['failures'][0]['history']['verdict'], 'UNKNOWN')

    @mock.patch('sp_cli.client.ApiClient.get_paginated')
    @mock.patch('sp_cli.client.ApiClient.get')
    def test_table_output_lifts_the_verdict_into_a_column(self, mock_get, mock_paginated):
        """The nested block is right for JSON and wrong for a table cell."""
        result = self._invoke(mock_get, mock_paginated,
                              ['-o', 'table', 'investigate', '9299', '--with-history'],
                              {42: self._history_for(18), 43: self._history_for(137)})

        self.assertEqual(result.exit_code, 0)
        self.assertIn('verdict', result.output)
        self.assertIn('NEW_REGRESSION', result.output)
        self.assertIn('by history:', result.output)
        self.assertNotIn("{'verdict'", result.output)


class ApiContractTests(unittest.TestCase):
    """Pin the CLI surface to what the merged ``mod_api`` blueprint actually accepts.

    Every enumeration asserted here mirrors a validator in the platform repo, so
    a drift on either side should fail loudly instead of costing an HTTP 400.
    """

    def setUp(self):
        """Create a runner for each test."""
        self.runner = CliRunner()

    @mock.patch('sp_cli.client.ApiClient.get')
    def test_run_ls_rejects_statuses_the_api_does_not_support(self, mock_get):
        """`run ls --status` only offers queued/running/canceled, and rejects the rest locally."""
        for bad_status in ('pass', 'fail', 'error', 'incomplete'):
            result = self.runner.invoke(cli, ['run', 'ls', '--status', bad_status])
            self.assertNotEqual(result.exit_code, 0, f'{bad_status} should be rejected')
        mock_get.assert_not_called()

    @mock.patch('sp_cli.client.ApiClient.get')
    def test_run_ls_forwards_repository_sort_and_date_filters(self, mock_get):
        """`run ls` passes through every filter /runs declares."""
        mock_get.return_value = RUNS_PAGE
        result = self.runner.invoke(cli, [
            'run', 'ls', '--repository', 'CCExtractor/ccextractor', '--sort', '-created_at',
            '--created-after', '2026-07-01T00:00:00Z', '--created-before', '2026-07-31T00:00:00Z'])

        self.assertEqual(result.exit_code, 0)
        mock_get.assert_called_once_with('/runs', params={
            'repository': 'CCExtractor/ccextractor', 'sort': '-created_at',
            'created_after': '2026-07-01T00:00:00Z', 'created_before': '2026-07-31T00:00:00Z'})

    @mock.patch('sp_cli.client.ApiClient.get')
    def test_run_results_rejects_unsupported_status(self, mock_get):
        """'skipped' and 'running' are not in the API's _VALID_SAMPLE_STATUSES."""
        for bad_status in ('skipped', 'running'):
            result = self.runner.invoke(cli, ['run', 'results', '9299', '--status', bad_status])
            self.assertNotEqual(result.exit_code, 0, f'{bad_status} should be rejected')
        mock_get.assert_not_called()

    @mock.patch('sp_cli.client.ApiClient.get')
    def test_run_results_forwards_name_tag_and_category(self, mock_get):
        """`run results` supports the joined-field filters /runs/{id}/samples applies."""
        mock_get.return_value = {'data': [], 'pagination': {}}
        result = self.runner.invoke(cli, ['run', 'results', '9299', '--status', 'missing_output',
                                          '--name', 'dvb', '--tag', 'teletext', '--category', 'DVB'])

        self.assertEqual(result.exit_code, 0)
        mock_get.assert_called_once_with('/runs/9299/samples', params={
            'status': 'missing_output', 'name': 'dvb', 'tag': 'teletext', 'category': 'DVB'})

    @mock.patch('sp_cli.client.ApiClient.get')
    def test_run_progress_and_config(self, mock_get):
        """`run progress` and `run config` reach their merged endpoints."""
        mock_get.return_value = {'data': [], 'pagination': {}}
        self.assertEqual(self.runner.invoke(cli, ['run', 'progress', '9299']).exit_code, 0)
        mock_get.assert_called_with('/runs/9299/progress', params={})

        mock_get.return_value = {'run_id': 9299, 'platform': 'windows'}
        self.assertEqual(self.runner.invoke(cli, ['run', 'config', '9299']).exit_code, 0)
        mock_get.assert_called_with('/runs/9299/config', params=None)

    @mock.patch('sp_cli.client.ApiClient.request')
    def test_run_cancel_posts_with_reason(self, mock_request):
        """`run cancel` POSTs the reason when one is given."""
        mock_request.return_value = {'run_id': 9299, 'action': 'cancel', 'status': 'canceled'}
        result = self.runner.invoke(cli, ['run', 'cancel', '9299', '--reason', 'superseded by 9300'])

        self.assertEqual(result.exit_code, 0)
        mock_request.assert_called_once_with('POST', '/runs/9299/cancel',
                                             json_body={'reason': 'superseded by 9300'})

    @mock.patch('sp_cli.client.ApiClient.request')
    def test_run_cancel_omits_body_when_no_reason(self, mock_request):
        """No reason means no body, rather than a null the schema would reject."""
        mock_request.return_value = {'run_id': 9299, 'status': 'canceled'}
        result = self.runner.invoke(cli, ['run', 'cancel', '9299'])

        self.assertEqual(result.exit_code, 0)
        mock_request.assert_called_once_with('POST', '/runs/9299/cancel', json_body=None)

    @mock.patch('sp_cli.client.ApiClient.request')
    def test_run_cancel_rejects_short_reason_before_the_request(self, mock_request):
        """The API needs 5+ characters; catch it locally instead of round-tripping a 400."""
        result = self.runner.invoke(cli, ['run', 'cancel', '9299', '--reason', 'no'])

        self.assertNotEqual(result.exit_code, 0)
        mock_request.assert_not_called()

    @mock.patch('sp_cli.client.ApiClient.get')
    def test_run_output_resolves_ids_like_diff(self, mock_get):
        """`run output` reuses the diff resolver, so the hidden ids stay optional."""
        mock_get.side_effect = [
            {'regression_test_id': 137, 'sample_id': 42,
             'outputs': [{'output_id': 2, 'status': 'fail'}]},
            {'content': 'line', 'truncated': False},
        ]
        result = self.runner.invoke(cli, ['run', 'output', '9299', '5', '--side', 'expected'])

        self.assertEqual(result.exit_code, 0)
        args, kwargs = mock_get.call_args
        self.assertEqual(args[0], '/runs/9299/samples/42/regression-tests/137/outputs/2/expected')
        self.assertEqual(kwargs['params'], {})

    @mock.patch('sp_cli.client.ApiClient.request')
    def test_auth_login_rejects_lifetime_over_the_api_cap(self, mock_request):
        """expires_in_days is validated as Range(min=1, max=30) server-side."""
        result = self.runner.invoke(cli, ['auth', 'login', '--email', 'a@b.co',
                                          '--password', 'hunter22', '--days', '60'])

        self.assertNotEqual(result.exit_code, 0)
        mock_request.assert_not_called()

    @mock.patch('sp_cli.client.ApiClient.request')
    def test_auth_login_sends_scopes_only_when_requested(self, mock_request):
        """Omitting --scope leaves the field out so the server picks its default set."""
        mock_request.return_value = {'token': 'spci_x', 'token_name': 'sp-cli', 'scopes': []}
        # --no-save: this asserts the request body, not the saved session. The
        # conftest fixture makes it safe either way, but saying so locally keeps
        # the next reader from wondering whether the write is intentional.
        result = self.runner.invoke(cli, ['auth', 'login', '--email', 'a@b.co',
                                          '--password', 'hunter22', '--scope', 'runs:read',
                                          '--scope', 'results:read', '--no-save'])

        self.assertEqual(result.exit_code, 0)
        body = mock_request.call_args.kwargs['json_body']
        self.assertEqual(body['scopes'], ['runs:read', 'results:read'])
        self.assertEqual(body['expires_in_days'], 30)

    @mock.patch('sp_cli.client.ApiClient.get')
    def test_auth_tokens_lists_metadata(self, mock_get):
        """`auth tokens` reads the list endpoint."""
        mock_get.return_value = {'data': [], 'pagination': {}}
        result = self.runner.invoke(cli, ['auth', 'tokens'])

        self.assertEqual(result.exit_code, 0)
        mock_get.assert_called_once_with('/auth/tokens', params={})

    @mock.patch('sp_cli.client.ApiClient.request')
    def test_auth_revoke_targets_one_token(self, mock_request):
        """`auth revoke <id>` deletes that token, not the current one."""
        mock_request.return_value = None
        result = self.runner.invoke(cli, ['auth', 'revoke', '7'])

        self.assertEqual(result.exit_code, 0)
        mock_request.assert_called_once_with('DELETE', '/auth/tokens/7')

    @mock.patch('sp_cli.client.ApiClient.get')
    def test_sample_ls_forwards_sha256_and_catalog_status(self, mock_get):
        """`sample ls` covers every filter /samples declares."""
        mock_get.return_value = {'data': [], 'pagination': {}}
        result = self.runner.invoke(cli, ['sample', 'ls', '--sha256', 'abc123',
                                          '--status', 'inactive'])

        self.assertEqual(result.exit_code, 0)
        mock_get.assert_called_once_with('/samples',
                                         params={'sha256': 'abc123', 'status': 'inactive'})

    @mock.patch('sp_cli.client.ApiClient.get')
    def test_sample_history_forwards_branch_and_date_window(self, mock_get):
        """`sample history` supports the branch/date filters the endpoint applies."""
        mock_get.return_value = {'data': [], 'pagination': {}}
        result = self.runner.invoke(cli, ['sample', 'history', '42', '--branch', 'master',
                                          '--status', 'fail', '--created-after', '2026-07-01T00:00:00Z'])

        self.assertEqual(result.exit_code, 0)
        mock_get.assert_called_once_with('/samples/42/history', params={
            'branch': 'master', 'status': 'fail', 'created_after': '2026-07-01T00:00:00Z'})

    @mock.patch('sp_cli.client.ApiClient.get')
    def test_regression_ls_inactive_is_a_two_way_switch(self, mock_get):
        """--inactive sends active=false; omitting it lets the API default to active only."""
        mock_get.return_value = {'data': [], 'pagination': {}}
        self.assertEqual(self.runner.invoke(cli, ['regression', 'ls', '--inactive']).exit_code, 0)
        mock_get.assert_called_with('/regression-tests', params={'active': False})

        self.assertEqual(self.runner.invoke(cli, ['regression', 'ls']).exit_code, 0)
        mock_get.assert_called_with('/regression-tests', params={})

    @mock.patch('sp_cli.client.ApiClient.get')
    def test_queue_rejects_non_queue_status_and_paginates(self, mock_get):
        """/system/queue only knows queued and running, and accepts pagination."""
        self.assertNotEqual(self.runner.invoke(cli, ['queue', '--status', 'canceled']).exit_code, 0)

        mock_get.return_value = {'data': [], 'pagination': {}}
        result = self.runner.invoke(cli, ['queue', '--status', 'queued', '--limit', '10'])
        self.assertEqual(result.exit_code, 0)
        mock_get.assert_called_once_with('/system/queue', params={'status': 'queued', 'limit': 10})


class ErrorsLogsArtifactsTests(unittest.TestCase):
    """Cover the endpoints unblocked by sample-platform#1135 and #1141.

    These commands shipped as stubs while their PRs were open; the assertions
    here pin them to the contracts that actually landed.
    """

    def setUp(self):
        """Create a runner for each test."""
        self.runner = CliRunner()

    @mock.patch('sp_cli.client.ApiClient.get')
    def test_run_errors_forwards_every_declared_filter(self, mock_get):
        """`run errors` supports the type, severity, and sample_id filters the route applies."""
        mock_get.return_value = {'data': [], 'pagination': {}}
        result = self.runner.invoke(cli, [
            'run', 'errors', '9299', '--type', 'missing_output',
            '--severity', 'error', '--sample', '42', '--limit', '10'])

        self.assertEqual(result.exit_code, 0)
        mock_get.assert_called_once_with('/runs/9299/errors', params={
            'type': 'missing_output', 'severity': 'error', 'sample_id': 42, 'limit': 10})

    @mock.patch('sp_cli.client.ApiClient.get')
    def test_run_errors_rejects_types_the_service_cannot_emit(self, mock_get):
        """derive_errors_for_run only produces three types; 'test_failure' is not one."""
        for bad_type in ('test_failure', 'segfault', 'timeout'):
            result = self.runner.invoke(cli, ['run', 'errors', '9299', '--type', bad_type])
            self.assertNotEqual(result.exit_code, 0, f'{bad_type} should be rejected')
        mock_get.assert_not_called()

    @mock.patch('sp_cli.client.ApiClient.get')
    def test_error_summary_group_by_is_a_closed_set(self, mock_get):
        """group_by outside the API's four keys is a 400, so reject it locally."""
        self.assertNotEqual(
            self.runner.invoke(cli, ['run', 'error-summary', '9299', '--group-by', 'run']).exit_code, 0)
        mock_get.assert_not_called()

        mock_get.return_value = {'data': [], 'pagination': {}}
        result = self.runner.invoke(cli, ['run', 'error-summary', '9299', '--group-by', 'regression_id'])
        self.assertEqual(result.exit_code, 0)
        mock_get.assert_called_once_with('/runs/9299/error-summary',
                                         params={'group_by': 'regression_id'})

    @mock.patch('sp_cli.client.ApiClient.get')
    def test_infra_errors_target_the_hyphenated_path_and_gate_stacks(self, mock_get):
        """The route is /infrastructure-errors, and include_stack is opt-in as a string."""
        mock_get.return_value = {'data': [], 'pagination': {}}
        result = self.runner.invoke(cli, ['run', 'infra-errors', '9299', '--type', 'vm_provisioning'])
        self.assertEqual(result.exit_code, 0)
        mock_get.assert_called_once_with('/runs/9299/infrastructure-errors',
                                         params={'type': 'vm_provisioning'})

        mock_get.reset_mock()
        result = self.runner.invoke(cli, ['run', 'infra-errors', '9299', '--include-stack'])
        self.assertEqual(result.exit_code, 0)
        mock_get.assert_called_once_with('/runs/9299/infrastructure-errors',
                                         params={'include_stack': 'true'})

    @mock.patch('sp_cli.client.ApiClient.get')
    def test_infra_error_type_is_restricted_to_the_classifier_buckets(self, mock_get):
        """_classify_infra_error only ever returns six values."""
        result = self.runner.invoke(cli, ['run', 'infra-errors', '9299', '--type', 'network'])
        self.assertNotEqual(result.exit_code, 0)
        mock_get.assert_not_called()

    @mock.patch('sp_cli.client.ApiClient.get')
    def test_run_artifacts_filters_by_type(self, mock_get):
        """`run artifacts --type` mirrors the artifact kinds list_artifacts builds."""
        mock_get.return_value = {'data': [], 'pagination': {}}
        result = self.runner.invoke(cli, ['run', 'artifacts', '9299', '--type', 'coredump'])

        self.assertEqual(result.exit_code, 0)
        mock_get.assert_called_once_with('/runs/9299/artifacts', params={'type': 'coredump'})

        self.assertNotEqual(
            self.runner.invoke(cli, ['run', 'artifacts', '9299', '--type', 'stderr']).exit_code, 0)

    @mock.patch('sp_cli.client.ApiClient.get')
    def test_run_logs_use_cursor_pagination_never_offset(self, mock_get):
        """The logs route 400s if offset is present, so the CLI exposes --cursor only."""
        mock_get.return_value = {'data': [], 'pagination': {'limit': 100, 'next_cursor': None}}
        result = self.runner.invoke(cli, [
            'run', 'logs', '9299', '--level', 'error', '--source', 'worker',
            '--contains', 'segfault', '--limit', '50', '--cursor', '400'])

        self.assertEqual(result.exit_code, 0)
        mock_get.assert_called_once_with('/runs/9299/logs', params={
            'level': 'error', 'source': 'worker', 'contains': 'segfault',
            'limit': 50, 'cursor': '400'})

        self.assertNotEqual(
            self.runner.invoke(cli, ['run', 'logs', '9299', '--offset', '10']).exit_code, 0)

    @mock.patch('sp_cli.client.ApiClient.get')
    def test_run_logs_rejects_an_over_long_contains_before_the_request(self, mock_get):
        """The API caps contains at 100 characters; fail locally instead of round-tripping."""
        result = self.runner.invoke(cli, ['run', 'logs', '9299', '--contains', 'x' * 101])

        self.assertNotEqual(result.exit_code, 0)
        mock_get.assert_not_called()

    @mock.patch('sp_cli.client.ApiClient.get')
    def test_run_logs_all_follows_the_cursor_to_the_end(self, mock_get):
        """--all pages through next_cursor and collapses the result into one list."""
        mock_get.side_effect = [
            {'data': [{'message': 'first'}], 'pagination': {'next_cursor': '1'}},
            {'data': [{'message': 'second'}], 'pagination': {'next_cursor': None}},
        ]
        result = self.runner.invoke(cli, ['run', 'logs', '9299', '--all'])

        self.assertEqual(result.exit_code, 0)
        self.assertEqual(mock_get.call_args_list, [
            mock.call('/runs/9299/logs', params={}),
            mock.call('/runs/9299/logs', params={'cursor': '1'}),
        ])
        payload = json.loads(result.output)
        self.assertEqual([line['message'] for line in payload['data']], ['first', 'second'])
        self.assertEqual(payload['summary']['lines'], 2)

    @mock.patch('sp_cli.client.ApiClient.get')
    def test_run_logs_all_and_cursor_are_mutually_exclusive(self, mock_get):
        """--all restarts from the top, so combining it with --cursor is a usage error."""
        result = self.runner.invoke(cli, ['run', 'logs', '9299', '--all', '--cursor', '40'])

        self.assertNotEqual(result.exit_code, 0)
        mock_get.assert_not_called()

    @mock.patch('sp_cli.client.ApiClient.get')
    def test_missing_log_file_is_distinguishable_from_a_missing_run(self, mock_get):
        """A cold-storage log 404s as log_not_found, which the envelope must preserve."""
        mock_get.side_effect = ApiError(
            'log_not_found', 'Log file for run 9299 is not available locally.', 404,
            {'run_id': 9299, 'action_required': 'Use GET /runs/9299/artifacts (type=build_log)'})
        result = self.runner.invoke(cli, ['run', 'logs', '9299'])

        self.assertEqual(result.exit_code, 4)
        envelope = json.loads(result.stderr)
        self.assertEqual(envelope['error']['code'], 'log_not_found')
        self.assertIn('artifacts', envelope['error']['details']['action_required'])


class WriteEndpointTests(unittest.TestCase):
    """Cover the write and admin endpoints, pinned to the merged request schemas."""

    def setUp(self):
        """Create a runner for each test."""
        self.runner = CliRunner()

    @mock.patch('sp_cli.client.ApiClient.request')
    def test_run_create_posts_the_full_body(self, mock_request):
        """`run create` maps its options onto RunCreateRequestSchema field names."""
        mock_request.return_value = {'run_id': 9300, 'status': 'queued'}
        sha = 'a' * 40
        result = self.runner.invoke(cli, [
            'run', 'create', '--commit', sha, '--platform', 'linux',
            '--repository', 'CCExtractor/ccextractor', '--branch', 'feature/x',
            '--pull-request', '42', '--test', '18', '--test', '137'])

        self.assertEqual(result.exit_code, 0)
        mock_request.assert_called_once_with('POST', '/runs', json_body={
            'commit_sha': sha, 'platform': 'linux',
            'repository': 'CCExtractor/ccextractor', 'branch': 'feature/x',
            'pull_request': 42, 'regression_test_ids': [18, 137]})

    @mock.patch('sp_cli.client.ApiClient.request')
    def test_run_create_rejects_a_short_sha_locally(self, mock_request):
        """commit_sha is validated as 40 hex chars, so a short SHA never leaves the machine."""
        for bad in ('e6cd34e', 'z' * 40, 'a' * 39):
            result = self.runner.invoke(cli, [
                'run', 'create', '--commit', bad, '--platform', 'linux',
                '--repository', 'CCExtractor/ccextractor'])
            self.assertNotEqual(result.exit_code, 0, f'{bad!r} should be rejected')
        mock_request.assert_not_called()

    @mock.patch('sp_cli.client.ApiClient.request')
    def test_run_create_rejects_a_malformed_repository(self, mock_request):
        """repository must be owner/repo, not a bare name or a URL."""
        for bad in ('ccextractor', 'https://github.com/CCExtractor/ccextractor'):
            result = self.runner.invoke(cli, [
                'run', 'create', '--commit', 'a' * 40, '--platform', 'linux',
                '--repository', bad])
            self.assertNotEqual(result.exit_code, 0, f'{bad!r} should be rejected')
        mock_request.assert_not_called()

    @mock.patch('sp_cli.client.ApiClient.request')
    def test_regression_create_sends_categories_by_name(self, mock_request):
        """Categories are given by name and are required; active defaults off, like the API."""
        mock_request.return_value = {'id': 5}
        result = self.runner.invoke(cli, [
            'regression', 'create', '--sample-id', '42', '--command', '-autoprogram',
            '--category', 'DVB', '--category', 'General'])

        self.assertEqual(result.exit_code, 0)
        mock_request.assert_called_once_with('POST', '/regression-tests', json_body={
            'sample_id': 42, 'command': '-autoprogram', 'categories': ['DVB', 'General']})

    @mock.patch('sp_cli.client.ApiClient.request')
    def test_regression_create_sends_active_only_when_asked(self, mock_request):
        """The API's load_default is False, so a bare create must not send active at all."""
        mock_request.return_value = {'id': 5}
        result = self.runner.invoke(cli, [
            'regression', 'create', '--sample-id', '42', '--command', 'x',
            '--category', 'DVB', '--active'])

        self.assertEqual(result.exit_code, 0)
        self.assertTrue(mock_request.call_args.kwargs['json_body']['active'])

    @mock.patch('sp_cli.client.ApiClient.request')
    def test_regression_edit_requires_at_least_one_field(self, mock_request):
        """An empty PATCH body is a usage error, not a pointless round trip."""
        result = self.runner.invoke(cli, ['regression', 'edit', '18'])
        self.assertNotEqual(result.exit_code, 0)
        mock_request.assert_not_called()

    @mock.patch('sp_cli.client.ApiClient.request')
    def test_regression_edit_patches_only_given_fields(self, mock_request):
        """PATCH is sparse: untouched options must not appear in the body."""
        mock_request.return_value = {'id': 18}
        result = self.runner.invoke(cli, ['regression', 'edit', '18', '--inactive'])

        self.assertEqual(result.exit_code, 0)
        mock_request.assert_called_once_with(
            'PATCH', '/regression-tests/18', json_body={'active': False})

    @mock.patch('sp_cli.client.ApiClient.request')
    def test_regression_rm_confirms_before_deleting(self, mock_request):
        """Deletion prompts unless --yes; declining must not issue the request."""
        result = self.runner.invoke(cli, ['regression', 'rm', '18'], input='n\n')
        self.assertNotEqual(result.exit_code, 0)
        mock_request.assert_not_called()

        mock_request.return_value = {'id': 18, 'deleted': True}
        result = self.runner.invoke(cli, ['regression', 'rm', '18', '--yes'])
        self.assertEqual(result.exit_code, 0)
        mock_request.assert_called_once_with('DELETE', '/regression-tests/18', json_body=None)

    @mock.patch('sp_cli.client.ApiClient.request')
    def test_a_refused_delete_maps_to_its_own_exit_code(self, mock_request):
        """409 means the world disagreed, not that the body was wrong -- exit 8, not 5."""
        mock_request.side_effect = ApiError(
            'conflict', 'Regression test 18 has 12 historical result(s).', 409,
            {'result_count': 12})
        result = self.runner.invoke(cli, ['regression', 'rm', '18', '--yes'])

        self.assertEqual(result.exit_code, 8)
        self.assertEqual(json.loads(result.stderr)['error']['code'], 'conflict')

    @mock.patch('sp_cli.client.ApiClient.get')
    def test_category_ls(self, mock_get):
        """`category ls` reaches /categories with pagination."""
        mock_get.return_value = {'data': [], 'pagination': {}}
        result = self.runner.invoke(cli, ['category', 'ls', '--limit', '10'])

        self.assertEqual(result.exit_code, 0)
        mock_get.assert_called_once_with('/categories', params={'limit': 10})

    @mock.patch('sp_cli.client.ApiClient.request')
    def test_category_create_and_edit(self, mock_request):
        """Create takes a positional name; edit is sparse and needs a field."""
        mock_request.return_value = {'id': 3, 'name': 'DVB'}
        result = self.runner.invoke(cli, ['category', 'create', 'DVB', '--description', 'DVB subs'])
        self.assertEqual(result.exit_code, 0)
        mock_request.assert_called_once_with(
            'POST', '/categories', json_body={'name': 'DVB', 'description': 'DVB subs'})

        self.assertNotEqual(self.runner.invoke(cli, ['category', 'edit', '3']).exit_code, 0)

    @mock.patch('sp_cli.client.ApiClient.request')
    def test_category_name_width_is_enforced_locally(self, mock_request):
        """The name column is 64 chars; a longer one is rejected before the request."""
        result = self.runner.invoke(cli, ['category', 'create', 'x' * 65])
        self.assertNotEqual(result.exit_code, 0)
        mock_request.assert_not_called()

    @mock.patch('sp_cli.client.ApiClient.get')
    def test_sample_details(self, mock_get):
        """`sample details` is a distinct endpoint from `sample show`."""
        mock_get.return_value = {'sample_id': 42, 'media_info': None}
        result = self.runner.invoke(cli, ['sample', 'details', '42'])

        self.assertEqual(result.exit_code, 0)
        mock_get.assert_called_once_with('/samples/42/details', params=None)

    @mock.patch('sp_cli.client.ApiClient.get')
    def test_auth_whoami_and_users(self, mock_get):
        """whoami hits /auth/me; users hits /users."""
        mock_get.return_value = {'user_id': 1, 'role': 'admin'}
        self.assertEqual(self.runner.invoke(cli, ['auth', 'whoami']).exit_code, 0)
        mock_get.assert_called_with('/auth/me', params=None)

        mock_get.return_value = {'data': [], 'pagination': {}}
        self.assertEqual(self.runner.invoke(cli, ['auth', 'users']).exit_code, 0)
        mock_get.assert_called_with('/users', params={})

    @mock.patch('sp_cli.client.ApiClient.request')
    def test_auth_set_role_is_restricted_to_the_role_enum(self, mock_request):
        """PATCH /users/{id} validates against Role, so reject anything else locally."""
        self.assertNotEqual(
            self.runner.invoke(cli, ['auth', 'set-role', '5', 'superuser']).exit_code, 0)
        mock_request.assert_not_called()

        mock_request.return_value = {'user_id': 5, 'role': 'contributor'}
        result = self.runner.invoke(cli, ['auth', 'set-role', '5', 'contributor'])
        self.assertEqual(result.exit_code, 0)
        mock_request.assert_called_once_with(
            'PATCH', '/users/5', json_body={'role': 'contributor'})

    @mock.patch('sp_cli.client.ApiClient.request')
    def test_admin_pause_and_resume_send_the_disabled_flag(self, mock_request):
        """Pause and resume are the same PATCH with opposite booleans."""
        mock_request.return_value = {'platform': 'linux', 'disabled': True}
        self.assertEqual(self.runner.invoke(cli, ['admin', 'pause', 'linux']).exit_code, 0)
        mock_request.assert_called_with(
            'PATCH', '/system/maintenance/linux', json_body={'disabled': True})

        self.assertEqual(self.runner.invoke(cli, ['admin', 'resume', 'linux']).exit_code, 0)
        mock_request.assert_called_with(
            'PATCH', '/system/maintenance/linux', json_body={'disabled': False})

        self.assertNotEqual(self.runner.invoke(cli, ['admin', 'pause', 'bsd']).exit_code, 0)

    @mock.patch('sp_cli.client.ApiClient.request')
    def test_blocked_user_add_takes_the_numeric_github_id(self, mock_request):
        """The API keys on the numeric id, since logins can be changed and reused."""
        mock_request.return_value = {'user_id': 1234, 'comment': 'spam'}
        result = self.runner.invoke(cli, ['admin', 'blocked-users', 'add', '1234',
                                          '--comment', 'spam'])

        self.assertEqual(result.exit_code, 0)
        mock_request.assert_called_once_with(
            'POST', '/system/blocked-users', json_body={'user_id': 1234, 'comment': 'spam'})

    @mock.patch('sp_cli.client.ApiClient.request')
    def test_forbidden_extension_is_normalized_before_sending(self, mock_request):
        """Stored without a leading dot and lower-cased, so normalize on the way out."""
        mock_request.return_value = {'extension': 'mkv'}
        result = self.runner.invoke(cli, ['admin', 'forbidden-extensions', 'add', '.MKV'])

        self.assertEqual(result.exit_code, 0)
        mock_request.assert_called_once_with(
            'POST', '/system/forbidden-extensions', json_body={'extension': 'mkv'})

        self.assertNotEqual(
            self.runner.invoke(cli, ['admin', 'forbidden-extensions', 'add', 'mk*v']).exit_code, 0)


class ScopeContractTests(unittest.TestCase):
    """Pin the token scope list, which drifted once and broke every admin write.

    `system:write` was missing from TOKEN_SCOPES, so Click rejected
    `--scope system:write` as an invalid choice and no CLI-created token could
    ever authorize `sp admin pause` / `blocked-users` / `forbidden-extensions`.
    Mocked tests could not catch it; only a live call did.
    """

    def setUp(self):
        """Create a runner for each test."""
        self.runner = CliRunner()

    def test_every_valid_scope_is_offered(self):
        """The list must match mod_api.models.api_token.VALID_SCOPES exactly."""
        from sp_cli.constants import TOKEN_MAX_SCOPES, TOKEN_SCOPES

        self.assertEqual(set(TOKEN_SCOPES), {
            'runs:read', 'runs:write', 'results:read', 'baselines:write',
            'system:read', 'system:write', 'tokens:manage',
        })
        # The API validates Length(max=len(VALID_SCOPES)), so asking for
        # everything you are allowed must never fail validation.
        self.assertEqual(TOKEN_MAX_SCOPES, len(TOKEN_SCOPES))

    @mock.patch('sp_cli.client.ApiClient.request')
    def test_system_write_is_accepted_by_login(self, mock_request):
        """Without this scope every `sp admin` write command 403s."""
        mock_request.return_value = {'token': 'x', 'scopes': ['system:write']}
        result = self.runner.invoke(cli, [
            'auth', 'login', '--email', 'a@b.c', '--password', 'pw',
            '--scope', 'system:write', '--no-save'])

        self.assertEqual(result.exit_code, 0)
        self.assertEqual(mock_request.call_args.kwargs['json_body']['scopes'],
                         ['system:write'])

    @mock.patch('sp_cli.client.ApiClient.request')
    def test_asking_for_every_scope_at_once_is_allowed(self, mock_request):
        """The cap is len(VALID_SCOPES), so a full-access token is requestable."""
        from sp_cli.constants import TOKEN_SCOPES

        mock_request.return_value = {'token': 'x', 'scopes': list(TOKEN_SCOPES)}
        args = ['auth', 'login', '--email', 'a@b.c', '--password', 'pw', '--no-save']
        for scope in TOKEN_SCOPES:
            args += ['--scope', scope]
        result = self.runner.invoke(cli, args)

        self.assertEqual(result.exit_code, 0)
        self.assertEqual(len(mock_request.call_args.kwargs['json_body']['scopes']),
                         len(TOKEN_SCOPES))


class OutputDecodeTests(unittest.TestCase):
    """`run output --decode` writes the file itself, not the JSON envelope.

    The API returns subtitle files base64-encoded inside JSON. Without --decode
    the terminal gets a multi-kilobyte blob that no one can read and no diff
    tool can consume.
    """

    def setUp(self):
        """Create a runner for each test."""
        self.runner = CliRunner()

    #: What /runs/{id}/samples/{id} answers, so the ids can be resolved.
    DETAIL = {'sample_id': 11, 'regression_test_id': 11,
              'outputs': [{'output_id': 11, 'status': 'fail'}]}

    @mock.patch('sp_cli.client.ApiClient.get')
    def test_decode_writes_the_raw_file(self, mock_get):
        """The base64 payload comes back out as the original bytes."""
        import base64
        body = '1\r\n00:00:01,000 --> 00:00:02,000\r\nHELLO\r\n'
        mock_get.side_effect = [
            self.DETAIL,
            {'content': base64.b64encode(body.encode()).decode(), 'encoding': 'base64'},
        ]
        result = self.runner.invoke(cli, ['run', 'output', '9388', '11', '--decode'])

        self.assertEqual(result.exit_code, 0)
        # Asserted on bytes: CRLF is what a subtitle file actually contains, and
        # result.stdout would normalise it away and hide a re-encoding bug.
        self.assertEqual(result.stdout_bytes, body.encode())
        # No JSON envelope leaked alongside the file.
        self.assertNotIn(b'"encoding"', result.stdout_bytes)

    @mock.patch('sp_cli.client.ApiClient.get')
    def test_plain_content_is_passed_through(self, mock_get):
        """An envelope that is not base64 is written as-is."""
        mock_get.side_effect = [self.DETAIL, {'content': 'already text', 'encoding': None}]
        result = self.runner.invoke(cli, ['run', 'output', '9388', '11', '--decode'])

        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.stdout, 'already text')

    @mock.patch('sp_cli.client.ApiClient.get')
    def test_without_the_flag_the_envelope_is_unchanged(self, mock_get):
        """The default stays JSON, so existing scripts are unaffected."""
        mock_get.side_effect = [self.DETAIL, {'content': 'eA==', 'encoding': 'base64'}]
        result = self.runner.invoke(cli, ['run', 'output', '9388', '11'])

        self.assertEqual(result.exit_code, 0)
        self.assertEqual(json.loads(result.stdout)['encoding'], 'base64')

    @mock.patch('sp_cli.client.ApiClient.get')
    def test_an_envelope_with_no_inline_content_is_an_error(self, mock_get):
        """A download-only artifact has nothing to decode; say so, don't crash."""
        mock_get.side_effect = [
            self.DETAIL,
            {'content': None, 'download_url': 'https://storage.example/x'},
        ]
        result = self.runner.invoke(cli, ['run', 'output', '9388', '11', '--decode'])

        self.assertEqual(result.exit_code, 4)
        envelope = json.loads(result.stderr)
        self.assertEqual(envelope['error']['code'], 'no_content')
        self.assertIn('download_url', envelope['error']['details'])


class HistoryDegradationTests(unittest.TestCase):
    """A failed history lookup must not discard the whole investigation.

    Found against production: /samples/{id}/history 504s there, and one failure
    aborted the entire command -- throwing away the run summary and all 45
    classified failures to report a single missing verdict.
    """

    def setUp(self):
        """Create a runner for each test."""
        self.runner = CliRunner()

    RUN = {'run_id': 9388, 'platform': 'linux', 'status': 'fail'}
    SUMMARY = {'fail_count': 2, 'total_samples': 2, 'pass_count': 0}

    @mock.patch('sp_cli.client.ApiClient.get_paginated')
    @mock.patch('sp_cli.client.ApiClient.get')
    def test_one_failed_lookup_does_not_lose_the_investigation(self, mock_get, mock_paginated):
        """The classified failures still come back; only that row is UNKNOWN."""
        mock_get.side_effect = [self.RUN, self.SUMMARY]
        mock_paginated.side_effect = [
            SAMPLES_WITH_IDS,
            ApiError('connection_error', 'Read timed out.'),
            [],
        ]
        result = self.runner.invoke(cli, ['investigate', '9388', '--with-history'])

        self.assertEqual(result.exit_code, 0, result.output)
        report = json.loads(result.stdout)
        self.assertEqual(len(report['failures']), 2)
        self.assertEqual(report['failures'][0]['history']['verdict'], 'UNKNOWN')
        self.assertIn(42, report['history_incomplete']['failed_samples'])

    @mock.patch('sp_cli.client.ApiClient.get_paginated')
    @mock.patch('sp_cli.client.ApiClient.get')
    def test_it_stops_asking_after_repeated_failures(self, mock_get, mock_paginated):
        """A down endpoint must not cost one full timeout per sample."""
        many = [dict(s, sample_id=100 + i, regression_test_id=200 + i)
                for i, s in enumerate(SAMPLES_WITH_IDS * 5)]
        mock_get.side_effect = [self.RUN, self.SUMMARY]
        mock_paginated.side_effect = [many] + [
            ApiError('connection_error', 'Read timed out.') for _ in range(20)]

        result = self.runner.invoke(cli, ['investigate', '9388', '--with-history'])

        self.assertEqual(result.exit_code, 0)
        report = json.loads(result.stdout)
        self.assertTrue(report['history_incomplete']['gave_up'])
        # 1 call for the samples list + at most the failure limit before giving up.
        self.assertLessEqual(mock_paginated.call_count, 4)
        self.assertEqual(len(report['failures']), len(many))

    @mock.patch('sp_cli.client.ApiClient.get_paginated')
    @mock.patch('sp_cli.client.ApiClient.get')
    def test_a_clean_run_reports_no_gap(self, mock_get, mock_paginated):
        """history_incomplete is absent when every lookup succeeded."""
        mock_get.side_effect = [self.RUN, self.SUMMARY]
        mock_paginated.side_effect = [SAMPLES_WITH_IDS, [], []]
        result = self.runner.invoke(cli, ['investigate', '9388', '--with-history'])

        self.assertEqual(result.exit_code, 0)
        self.assertNotIn('history_incomplete', json.loads(result.stdout))

    @mock.patch('sp_cli.client.ApiClient.get_paginated')
    @mock.patch('sp_cli.client.ApiClient.get')
    def test_the_run_itself_failing_is_still_fatal(self, mock_get, mock_paginated):
        """Degrading history is fine; a missing run means there is no answer at all."""
        mock_get.side_effect = ApiError('not_found', 'Run 1 not found', 404)
        result = self.runner.invoke(cli, ['investigate', '1', '--with-history'])

        self.assertEqual(result.exit_code, 4)


class HistoryDegradationOrderingTests(unittest.TestCase):
    """The three interacting defects in the history circuit breaker.

    Each of these passed the original implementation's own tests, because those
    only exercised one failing sample at a time.
    """

    def setUp(self):
        """Create a runner for each test."""
        self.runner = CliRunner()

    RUN = {'run_id': 9388, 'platform': 'linux', 'status': 'fail'}
    SUMMARY = {'fail_count': 4, 'total_samples': 4, 'pass_count': 0}

    @staticmethod
    def _rows(*pairs):
        """Build failure rows as (sample_id, regression_test_id) pairs."""
        return [{'sample_id': s, 'regression_test_id': r, 'sample_name': f's{s}',
                 'categories': [], 'status': 'fail', 'exit_code': 1,
                 'expected_rc': 0, 'outputs': []} for s, r in pairs]

    @mock.patch('sp_cli.client.ApiClient.get_paginated')
    @mock.patch('sp_cli.client.ApiClient.get')
    def test_one_bad_sample_does_not_trip_the_breaker(self, mock_get, mock_paginated):
        """A failure is remembered per sample, so shared samples are asked once."""
        # Sample 50 has three regression tests and is unreachable; 60 is fine.
        rows = self._rows((50, 1), (50, 2), (50, 3), (60, 4))
        mock_get.side_effect = [self.RUN, self.SUMMARY]
        mock_paginated.side_effect = [
            rows,
            ApiError('connection_error', 'Read timed out.'),
            [],  # sample 60 resolves normally
        ]
        result = self.runner.invoke(cli, ['investigate', '9388', '--with-history'])

        self.assertEqual(result.exit_code, 0)
        report = json.loads(result.stdout)
        # Sample 50 asked once, not three times: 1 samples call + 2 history calls.
        self.assertEqual(mock_paginated.call_count, 3)
        self.assertFalse(report['history_incomplete']['gave_up'])
        self.assertEqual(report['history_incomplete']['failed_samples'], [50])
        # Sample 60 still got a real verdict.
        self.assertNotEqual(report['failures'][3]['history']['verdict'], 'UNKNOWN')

    @mock.patch('sp_cli.client.ApiClient.get_paginated')
    @mock.patch('sp_cli.client.ApiClient.get')
    def test_giving_up_still_uses_history_already_in_hand(self, mock_get, mock_paginated):
        """A sample fetched successfully is classified even after the breaker trips."""
        # 42 succeeds first; 60/61/62 then fail and trip the breaker; 42 recurs.
        rows = self._rows((42, 1), (60, 2), (61, 3), (62, 4), (42, 5))
        mock_get.side_effect = [self.RUN, self.SUMMARY]
        mock_paginated.side_effect = [
            rows,
            [],  # sample 42 ok
            ApiError('connection_error', 'boom'),
            ApiError('connection_error', 'boom'),
            ApiError('connection_error', 'boom'),
        ]
        result = self.runner.invoke(cli, ['investigate', '9388', '--with-history'])

        self.assertEqual(result.exit_code, 0)
        report = json.loads(result.stdout)
        self.assertTrue(report['history_incomplete']['gave_up'])
        # The second row for sample 42 is classified from cache, not skipped.
        last = report['failures'][4]['history']
        self.assertNotEqual(last['verdict'], 'UNKNOWN')
        self.assertNotIn('not responding', str(last.get('reason', '')))

    @mock.patch('sp_cli.client.ApiClient.get_paginated')
    @mock.patch('sp_cli.client.ApiClient.get')
    def test_failed_samples_are_not_listed_twice(self, mock_get, mock_paginated):
        """One unreachable sample appears once in the report, however many rows it has."""
        rows = self._rows((50, 1), (50, 2), (50, 3))
        mock_get.side_effect = [self.RUN, self.SUMMARY]
        mock_paginated.side_effect = [rows, ApiError('connection_error', 'boom')]
        result = self.runner.invoke(cli, ['investigate', '9388', '--with-history'])

        self.assertEqual(result.exit_code, 0)
        self.assertEqual(
            json.loads(result.stdout)['history_incomplete']['failed_samples'], [50])

    @mock.patch('sp_cli.client.ApiClient.get_paginated')
    def test_history_depth_is_bounded_by_the_api_page_limit(self, mock_paginated):
        """limit = depth + 1 must stay <= 100, or every lookup 400s."""
        for bad in ('100', '500', '0', '-1'):
            result = self.runner.invoke(cli, ['investigate', '9388', '--history-depth', bad])
            self.assertNotEqual(result.exit_code, 0, f'--history-depth {bad} should be rejected')
        mock_paginated.assert_not_called()

    @mock.patch('sp_cli.client.ApiClient.get_paginated')
    @mock.patch('sp_cli.client.ApiClient.get')
    def test_the_largest_accepted_depth_still_fits_the_page(self, mock_get, mock_paginated):
        """depth 99 sends limit 100, which is exactly the API's ceiling."""
        mock_get.side_effect = [self.RUN, self.SUMMARY]
        mock_paginated.side_effect = [self._rows((42, 1)), []]
        result = self.runner.invoke(cli, ['investigate', '9388', '--history-depth', '99'])

        self.assertEqual(result.exit_code, 0)
        self.assertEqual(mock_paginated.call_args.kwargs['params']['limit'], 100)


class TruncatedOutputTests(unittest.TestCase):
    """`--decode` must not write a file that ends mid-stream and looks complete.

    The API inlines at most 1 MiB and flags the rest as truncated; writing the
    fragment made every diff against it report spurious missing lines.
    """

    def setUp(self):
        """Create a runner for each test."""
        self.runner = CliRunner()

    DETAIL = {'sample_id': 11, 'regression_test_id': 11,
              'outputs': [{'output_id': 11, 'status': 'fail'}]}

    BIG = {'content': 'dHJ1bmNhdGVk', 'encoding': 'base64', 'truncated': True,
           'sha256': 'abc123', 'download_url': 'https://storage.example/whole-file'}

    @mock.patch('sp_cli.client.ApiClient.get')
    def test_a_truncated_output_is_refused_not_silently_written(self, mock_get):
        """Nothing reaches stdout, and the error names where to get the whole file."""
        mock_get.side_effect = [self.DETAIL, self.BIG]
        result = self.runner.invoke(cli, ['run', 'output', '9388', '11', '--decode'])

        self.assertNotEqual(result.exit_code, 0)
        self.assertEqual(result.stdout_bytes, b'')
        envelope = json.loads(result.stderr)
        self.assertEqual(envelope['error']['code'], 'output_truncated')
        self.assertEqual(envelope['error']['details']['download_url'],
                         'https://storage.example/whole-file')

    @mock.patch('sp_cli.client.ApiClient.get')
    def test_allow_truncated_opts_in_explicitly(self, mock_get):
        """The capability is kept, but you have to ask for it by name."""
        mock_get.side_effect = [self.DETAIL, self.BIG]
        result = self.runner.invoke(
            cli, ['run', 'output', '9388', '11', '--decode', '--allow-truncated'])

        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.stdout_bytes, b'truncated')

    @mock.patch('sp_cli.client.ApiClient.get')
    def test_a_complete_output_is_unaffected(self, mock_get):
        """The ordinary path still writes the file."""
        mock_get.side_effect = [
            self.DETAIL,
            {'content': 'aGVsbG8=', 'encoding': 'base64', 'truncated': False},
        ]
        result = self.runner.invoke(cli, ['run', 'output', '9388', '11', '--decode'])

        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.stdout_bytes, b'hello')

    @mock.patch('sp_cli.client.ApiClient.get')
    def test_a_missing_truncated_key_is_treated_as_complete(self, mock_get):
        """Older responses without the field must not start failing."""
        mock_get.side_effect = [self.DETAIL, {'content': 'aGk=', 'encoding': 'base64'}]
        result = self.runner.invoke(cli, ['run', 'output', '9388', '11', '--decode'])

        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.stdout_bytes, b'hi')

    @mock.patch('sp_cli.client.ApiClient.get')
    def test_the_json_envelope_still_shows_truncation(self, mock_get):
        """Without --decode the flag is visible to the caller as data."""
        mock_get.side_effect = [self.DETAIL, self.BIG]
        result = self.runner.invoke(cli, ['run', 'output', '9388', '11'])

        self.assertEqual(result.exit_code, 0)
        self.assertTrue(json.loads(result.stdout)['truncated'])


class MachineOutputContractTests(unittest.TestCase):
    """Every command's stdout must be parseable JSON in the default mode.

    `auth revoke` and `auth logout` printed bare English sentences, so piping
    them into jq failed while every other command worked.
    """

    def setUp(self):
        """Create a runner for each test."""
        self.runner = CliRunner()

    @mock.patch('sp_cli.client.ApiClient.request')
    def test_auth_revoke_emits_json(self, mock_request):
        """`sp auth revoke 5 | jq .` must not choke on prose."""
        mock_request.return_value = None
        result = self.runner.invoke(cli, ['auth', 'revoke', '5'])

        self.assertEqual(result.exit_code, 0)
        payload = json.loads(result.stdout)
        self.assertEqual(payload, {'token_id': 5, 'revoked': True})

    @mock.patch('sp_cli.client.ApiClient.request')
    def test_auth_logout_emits_json(self, mock_request):
        """Same for logout, including whether the saved session went with it."""
        mock_request.return_value = None
        result = self.runner.invoke(cli, ['auth', 'logout'])

        self.assertEqual(result.exit_code, 0)
        payload = json.loads(result.stdout)
        self.assertIs(payload['revoked'], True)
        self.assertIn('saved_session_cleared', payload)

    @mock.patch('sp_cli.client.ApiClient.request')
    def test_no_command_leaks_prose_onto_stdout(self, mock_request):
        """A sweep over the write commands that answer 204, which have no body to render."""
        mock_request.return_value = None
        for args in (['auth', 'revoke', '5'], ['auth', 'logout']):
            result = self.runner.invoke(cli, args)
            self.assertEqual(result.exit_code, 0, args)
            try:
                json.loads(result.stdout)
            except ValueError:  # pragma: no cover - the assertion message is the point
                self.fail(f'{args} wrote non-JSON to stdout: {result.stdout!r}')


class PageLimitContractTests(unittest.TestCase):
    """--limit and --offset are bounded by what the API's paginator accepts.

    LOG_MAX_LIMIT mirrored a 1-500 clamp inside the log service, but
    _parse_limit rejects anything over 100 first, so that clamp is unreachable
    and the CLI advertised a page size the API refuses.
    """

    def setUp(self):
        """Create a runner for each test."""
        self.runner = CliRunner()

    @mock.patch('sp_cli.client.ApiClient.get')
    def test_run_logs_limit_is_capped_at_the_paginator_ceiling(self, mock_get):
        """The value the old help text advertised as legal is now rejected locally."""
        result = self.runner.invoke(cli, ['run', 'logs', '9299', '--limit', '500'])

        self.assertNotEqual(result.exit_code, 0)
        mock_get.assert_not_called()

    @mock.patch('sp_cli.client.ApiClient.get')
    def test_the_ceiling_itself_is_accepted(self, mock_get):
        """100 is legal; 101 is not."""
        mock_get.return_value = {'data': [], 'pagination': {}}
        self.assertEqual(
            self.runner.invoke(cli, ['run', 'logs', '9299', '--limit', '100']).exit_code, 0)
        self.assertNotEqual(
            self.runner.invoke(cli, ['run', 'logs', '9299', '--limit', '101']).exit_code, 0)

    @mock.patch('sp_cli.client.ApiClient.get')
    def test_every_paginated_command_rejects_an_out_of_range_limit(self, mock_get):
        """One shared rule, so it is checked across the surface rather than per command."""
        commands = (
            ['run', 'ls'], ['run', 'results', '9299'], ['run', 'errors', '9299'],
            ['run', 'artifacts', '9299'], ['run', 'progress', '9299'],
            ['sample', 'ls'], ['sample', 'history', '42'], ['regression', 'ls'],
            ['category', 'ls'], ['auth', 'tokens'], ['auth', 'users'], ['queue'],
            ['admin', 'blocked-users', 'ls'], ['admin', 'forbidden-extensions', 'ls'],
        )
        for cmd in commands:
            for bad in ('0', '101'):
                result = self.runner.invoke(cli, cmd + ['--limit', bad])
                self.assertNotEqual(result.exit_code, 0, f'{cmd} --limit {bad} should be rejected')
        mock_get.assert_not_called()

    @mock.patch('sp_cli.client.ApiClient.get')
    def test_a_negative_offset_is_rejected(self, mock_get):
        """The API 400s on a negative offset; fail locally instead."""
        result = self.runner.invoke(cli, ['run', 'ls', '--offset', '-1'])

        self.assertNotEqual(result.exit_code, 0)
        mock_get.assert_not_called()


class PrFilterTests(unittest.TestCase):
    """`run ls --pr` is filtered locally, because GET /runs has no such parameter.

    The API accepts only platform, branch, commit_sha, repository, status and
    the date window, so a pull request number cannot be pushed down. That makes
    two things worth pinning: matches must actually be filtered rather than the
    whole page returned, and an exhausted scan must be distinguishable from a
    pull request that genuinely has no runs.
    """

    def setUp(self):
        """Create a runner for each test."""
        self.runner = CliRunner()

    @staticmethod
    def _runs(*pr_numbers):
        """Build run rows carrying the given pr_number values."""
        return [{'run_id': 9000 + i, 'pr_number': pr, 'platform': 'linux', 'status': 'fail'}
                for i, pr in enumerate(pr_numbers)]

    @mock.patch('sp_cli.client.ApiClient.get_paginated')
    def test_only_matching_runs_are_returned(self, mock_paginated):
        """Rows for other pull requests are dropped, not merely sorted."""
        mock_paginated.return_value = self._runs(2309, 2290, 2309, None)
        result = self.runner.invoke(cli, ['run', 'ls', '--pr', '2309'])

        self.assertEqual(result.exit_code, 0)
        payload = json.loads(result.output)
        self.assertEqual(payload['total'], 2)
        self.assertEqual([row['pr_number'] for row in payload['data']], [2309, 2309])

    @mock.patch('sp_cli.client.ApiClient.get_paginated')
    def test_a_saturated_scan_is_reported(self, mock_paginated):
        """Zero matches from a full window is "none I saw", not "none exist"."""
        mock_paginated.return_value = self._runs(*([2290] * 5))
        result = self.runner.invoke(cli, ['run', 'ls', '--pr', '2109', '--max-scan', '5'])

        payload = json.loads(result.output)
        self.assertEqual(payload['total'], 0)
        self.assertEqual(payload['scanned'], 5)
        self.assertTrue(payload['scan_truncated'])

    @mock.patch('sp_cli.client.ApiClient.get_paginated')
    def test_an_exhausted_scan_is_not_reported_as_truncated(self, mock_paginated):
        """Fewer rows than the cap means the window really did end."""
        mock_paginated.return_value = self._runs(2290, 2290)
        result = self.runner.invoke(cli, ['run', 'ls', '--pr', '2109', '--max-scan', '5'])

        self.assertFalse(json.loads(result.output)['scan_truncated'])

    @mock.patch('sp_cli.client.ApiClient.get_paginated')
    def test_server_side_filters_still_reach_the_api(self, mock_paginated):
        """--platform narrows the scan itself; limit/offset drive it and are not forwarded."""
        mock_paginated.return_value = self._runs(2309)
        result = self.runner.invoke(cli, ['run', 'ls', '--pr', '2309', '--platform', 'linux'])

        self.assertEqual(result.exit_code, 0)
        _, kwargs = mock_paginated.call_args
        self.assertEqual(kwargs['params'], {'platform': 'linux'})

    @mock.patch('sp_cli.client.ApiClient.get_paginated')
    def test_limit_caps_matches_rather_than_the_page(self, mock_paginated):
        """With a local filter, --limit is only meaningful after filtering."""
        mock_paginated.return_value = self._runs(2309, 2309, 2309)
        result = self.runner.invoke(cli, ['run', 'ls', '--pr', '2309', '--limit', '2'])

        self.assertEqual(json.loads(result.output)['total'], 2)

    @mock.patch('sp_cli.client.ApiClient.get_paginated')
    def test_offset_is_refused(self, mock_paginated):
        """An offset indexes the unfiltered list, so it would skip matches silently."""
        result = self.runner.invoke(cli, ['run', 'ls', '--pr', '2309', '--offset', '10'])

        self.assertNotEqual(result.exit_code, 0)
        mock_paginated.assert_not_called()


class RunCompareTests(unittest.TestCase):
    """`run compare` wires the diff up to two runs and keeps the caveats visible."""

    def setUp(self):
        """Create a runner for each test."""
        self.runner = CliRunner()

    @staticmethod
    def _sample(test_id, status):
        """Build a minimal RunSample result."""
        return {'regression_test_id': test_id, 'sample_id': test_id,
                'sample_name': f'sample-{test_id}', 'status': status,
                'exit_code': 0, 'expected_rc': 0, 'outputs': []}

    def _invoke(self, run_samples, baseline_samples, run=None, baseline=None, args=()):
        """Run `run compare` against canned run details and results."""
        run = run or {'run_id': 1, 'platform': 'linux', 'commit_sha': 'a' * 40, 'status': 'fail'}
        baseline = baseline or {'run_id': 2, 'platform': 'linux',
                                'commit_sha': 'b' * 40, 'status': 'fail'}
        with mock.patch('sp_cli.client.ApiClient.get', side_effect=[run, baseline]), \
                mock.patch('sp_cli.client.ApiClient.get_paginated',
                           side_effect=[run_samples, baseline_samples]):
            return self.runner.invoke(cli, ['run', 'compare', '1', '2', *args])

    def test_reports_the_buckets(self):
        """A regression, a persistent failure and a repair, told apart."""
        result = self._invoke(
            [self._sample(1, 'fail'), self._sample(2, 'fail'), self._sample(3, 'pass')],
            [self._sample(1, 'pass'), self._sample(2, 'fail'), self._sample(3, 'fail')])

        self.assertEqual(result.exit_code, 0)
        payload = json.loads(result.output)
        self.assertEqual(payload['counts']['new'], 1)
        self.assertEqual(payload['counts']['still_failing'], 1)
        self.assertEqual(payload['counts']['fixed'], 1)

    def test_a_collapsed_run_does_not_report_the_baseline_as_fixed(self):
        """Run 9360 recorded 1 of 237 results while the API called it a pass."""
        result = self._invoke([], [self._sample(1, 'fail'), self._sample(2, 'fail')])

        payload = json.loads(result.output)
        self.assertEqual(payload['counts']['fixed'], 0)
        self.assertEqual(payload['counts']['not_rerun'], 2)
        self.assertTrue(any('not_rerun rather than fixed' in w
                            for w in payload['warnings']))

    def test_json_output_carries_both_run_headers(self):
        """A diff is meaningless without knowing which two runs produced it."""
        result = self._invoke([self._sample(1, 'fail')], [self._sample(1, 'pass')])

        payload = json.loads(result.output)
        self.assertEqual(payload['run']['run_id'], 1)
        self.assertEqual(payload['baseline']['run_id'], 2)


class BaseUrlPrecedenceTests(unittest.TestCase):
    """Where the API host comes from: --base-url > SP_BASE_URL > saved session > default.

    The default is the public deployment, because there is one canonical Sample
    Platform and `pip install` then `sp investigate <run>` should work. Anyone
    running their own instance overrides it, and a session logged in against one
    is remembered -- `sp auth login` already stored the URL beside the token,
    but nothing read it back, so a development token was sent to the default
    host instead.
    """

    def setUp(self):
        """Create a runner for each test."""
        self.runner = CliRunner()

    @staticmethod
    def _base_url_of(result_client):
        """Pull the base URL out of the client the group built."""
        return result_client.base_url

    def _run(self, argv, env=None, saved=None):
        """Invoke a command and capture the client the group constructed."""
        captured = {}

        def fake_get(self, path, params=None):
            captured['base_url'] = self.base_url
            return {'status': 'ok'}

        with mock.patch('sp_cli.client.ApiClient.get', fake_get), \
                mock.patch('sp_cli.config.saved_base_url', return_value=saved), \
                mock.patch('sp_cli.config.saved_token', return_value=None):
            result = self.runner.invoke(cli, argv, env=env or {})
        self.assertEqual(result.exit_code, 0, result.output)
        return captured['base_url']

    def test_default_is_the_public_deployment(self):
        """Nothing configured means the one deployment that actually exists."""
        self.assertEqual(self._run(['health']),
                         'https://sampleplatform.ccextractor.org/api/v1')

    def test_saved_session_beats_the_default(self):
        """A token issued by a development instance must go back to that instance."""
        self.assertEqual(self._run(['health'], saved='http://127.0.0.1:5058/api/v1'),
                         'http://127.0.0.1:5058/api/v1')

    def test_env_var_beats_a_saved_session(self):
        """SP_BASE_URL is the documented per-shell override."""
        self.assertEqual(
            self._run(['health'], env={'SP_BASE_URL': 'https://staging.example/api/v1'},
                      saved='http://127.0.0.1:5058/api/v1'),
            'https://staging.example/api/v1')

    def test_the_flag_beats_everything(self):
        """An explicit --base-url is the most specific thing the caller can say."""
        self.assertEqual(
            self._run(['--base-url', 'https://flag.example/api/v1', 'health'],
                      env={'SP_BASE_URL': 'https://env.example/api/v1'},
                      saved='http://127.0.0.1:5058/api/v1'),
            'https://flag.example/api/v1')

    def test_passing_the_default_explicitly_still_beats_a_saved_session(self):
        """Checked by parameter source, not by value, so this is not a saved-session case."""
        self.assertEqual(
            self._run(['--base-url', 'https://sampleplatform.ccextractor.org/api/v1', 'health'],
                      saved='http://127.0.0.1:5058/api/v1'),
            'https://sampleplatform.ccextractor.org/api/v1')
