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
    def test_history_depth_implies_the_flag_and_sets_the_page_size(self, mock_get, mock_paginated):
        """--history-depth alone turns history on, and asks for depth + the current run."""
        result = self._invoke(mock_get, mock_paginated,
                              ['investigate', '9299', '--history-depth', '5'],
                              {42: self._history_for(18), 43: self._history_for(137)})

        self.assertEqual(result.exit_code, 0)
        self.assertIn('by_verdict', json.loads(result.output))
        history_call = next(c for c in mock_paginated.call_args_list if '/history' in c.args[0])
        self.assertEqual(history_call.kwargs['params']['limit'], 6)
        self.assertEqual(history_call.kwargs['max_items'], 6)

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
        result = self.runner.invoke(cli, ['auth', 'login', '--email', 'a@b.co',
                                          '--password', 'hunter22', '--scope', 'runs:read',
                                          '--scope', 'results:read'])

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
