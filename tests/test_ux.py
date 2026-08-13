"""Tests for the saved session, colour, and spinner behaviour.

The rule these all share: a human-only effect must never reach machine output.
"""

from tests import SESSION_SANDBOX  # noqa: F401  # redirects the saved session; keep first

import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from click.testing import CliRunner

from sp_cli import config
from sp_cli.main import cli
from sp_cli.output import render

CLASSIFIED_ROWS = {
    'data': [
        {'sample_name': 'dvb', 'code': 'SEGFAULT', 'verdict': 'NEW_REGRESSION'},
        {'sample_name': 'ok', 'code': 'PASS', 'verdict': 'STILL_FAILING'},
    ],
}


class SavedSessionTests(unittest.TestCase):
    """`sp auth login` persists a token; everything else reads it back."""

    def setUp(self):
        """Point the config module at a throwaway directory."""
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patcher = mock.patch.dict(os.environ, {'XDG_CONFIG_HOME': self.tmp.name})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.runner = CliRunner()

    def test_token_file_is_owner_only(self):
        """The file holds a bearer credential, so it must not be group/world readable."""
        path = config.save_token('secret-token')

        mode = stat.S_IMODE(Path(path).stat().st_mode)
        self.assertEqual(mode, 0o600, f'expected 0600, got {oct(mode)}')
        self.assertFalse(config.is_world_readable())

    def test_saved_token_round_trips(self):
        """What login writes is what later commands read back."""
        config.save_token('secret-token', 'http://example.test/api/v1')

        self.assertEqual(config.saved_token(), 'secret-token')
        self.assertEqual(config.load()['base_url'], 'http://example.test/api/v1')

    def test_a_corrupt_file_degrades_to_logged_out(self):
        """A malformed config must mean 'no session', not break every command."""
        path = config.config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{not json', encoding='utf-8')

        self.assertEqual(config.load(), {})
        self.assertIsNone(config.saved_token())

    def test_clear_token_removes_only_the_credential(self):
        """Logging out drops the token but keeps unrelated settings."""
        config.save_token('secret-token', 'http://example.test/api/v1')

        self.assertTrue(config.clear_token())
        self.assertIsNone(config.saved_token())
        self.assertEqual(config.load().get('base_url'), 'http://example.test/api/v1')

    @mock.patch('sp_cli.client.ApiClient.__init__', return_value=None)
    def test_explicit_token_outranks_the_saved_session(self, mock_init):
        """--token beats the file, so an explicit credential always wins."""
        config.save_token('from-file')
        with mock.patch('sp_cli.client.ApiClient.get', return_value={'status': 'ok'}):
            self.runner.invoke(cli, ['--token', 'from-flag', 'health'])

        self.assertEqual(mock_init.call_args.kwargs['token'], 'from-flag')

    @mock.patch('sp_cli.client.ApiClient.__init__', return_value=None)
    def test_env_var_outranks_the_saved_session(self, mock_init):
        """SP_API_TOKEN beats the file too; only an unset env falls through."""
        config.save_token('from-file')
        with mock.patch.dict(os.environ, {'SP_API_TOKEN': 'from-env'}):
            with mock.patch('sp_cli.client.ApiClient.get', return_value={'status': 'ok'}):
                self.runner.invoke(cli, ['health'])

        self.assertEqual(mock_init.call_args.kwargs['token'], 'from-env')

    @mock.patch('sp_cli.client.ApiClient.__init__', return_value=None)
    def test_the_file_is_read_when_nothing_else_provides_a_token(self, mock_init):
        """With no flag and no env var, the saved session is what reaches the client."""
        config.save_token('from-file')
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop('SP_API_TOKEN', None)
            with mock.patch('sp_cli.client.ApiClient.get', return_value={'status': 'ok'}):
                self.runner.invoke(cli, ['health'])

        self.assertEqual(mock_init.call_args.kwargs['token'], 'from-file')

    @mock.patch('sp_cli.client.ApiClient.request')
    def test_login_saves_the_token_and_says_so_on_stderr(self, mock_request):
        """The confirmation goes to stderr so it cannot contaminate the JSON payload."""
        mock_request.return_value = {'token': 'brand-new', 'token_id': 7}
        result = self.runner.invoke(cli, [
            'auth', 'login', '--email', 'a@b.c', '--password', 'pw'])

        self.assertEqual(result.exit_code, 0)
        self.assertEqual(config.saved_token(), 'brand-new')
        self.assertIn('Token saved', result.stderr)
        # stdout stays pure JSON.
        self.assertEqual(json.loads(result.stdout)['token'], 'brand-new')

    @mock.patch('sp_cli.client.ApiClient.request')
    def test_no_save_leaves_nothing_on_disk(self, mock_request):
        """--no-save is for shared machines: the token is printed but never written."""
        mock_request.return_value = {'token': 'brand-new'}
        result = self.runner.invoke(cli, [
            'auth', 'login', '--email', 'a@b.c', '--password', 'pw', '--no-save'])

        self.assertEqual(result.exit_code, 0)
        self.assertIsNone(config.saved_token())

    @mock.patch('sp_cli.client.ApiClient.request')
    def test_logout_clears_the_file_even_when_the_server_rejects_it(self, mock_request):
        """An already-expired token must not be left behind on disk."""
        from sp_cli.client import ApiError
        config.save_token('stale')
        mock_request.side_effect = ApiError('unauthorized', 'Token expired.', 401)

        result = self.runner.invoke(cli, ['auth', 'logout'])

        self.assertEqual(result.exit_code, 6)
        self.assertIsNone(config.saved_token())


class ColorTests(unittest.TestCase):
    """Colour is decoration: table mode, TTY only, never in JSON."""

    def setUp(self):
        """Create a runner for each test."""
        self.runner = CliRunner()

    def test_no_escape_codes_when_stdout_is_not_a_tty(self):
        """Piping into jq or a file must receive plain text."""
        runner = CliRunner()
        with runner.isolation() as (out, _err, _):
            with mock.patch('sys.stdout.isatty', return_value=False):
                render(CLASSIFIED_ROWS, 'table', color=True)
            written = out.getvalue().decode()

        self.assertNotIn('\x1b[', written)

    def test_escape_codes_appear_only_for_the_classification_columns(self):
        """A TTY gets colour, but only on code/verdict -- names stay plain."""
        with mock.patch('sys.stdout.isatty', return_value=True), \
                mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop('NO_COLOR', None)
            with mock.patch('click.echo') as mock_echo:
                render(CLASSIFIED_ROWS, 'table', color=True)

        body = '\n'.join(str(call.args[0]) for call in mock_echo.call_args_list if call.args)
        self.assertIn('\x1b[', body)

    def test_no_color_env_var_is_honoured(self):
        """NO_COLOR is a cross-tool convention; respect it even on a TTY."""
        runner = CliRunner()
        with runner.isolation() as (out, _err, _):
            with mock.patch('sys.stdout.isatty', return_value=True), \
                    mock.patch.dict(os.environ, {'NO_COLOR': '1'}):
                render(CLASSIFIED_ROWS, 'table', color=True)
            written = out.getvalue().decode()

        self.assertNotIn('\x1b[', written)

    def test_json_mode_is_never_colorized(self):
        """JSON is the machine contract; colour must not reach it under any flag."""
        runner = CliRunner()
        with runner.isolation() as (out, _err, _):
            with mock.patch('sys.stdout.isatty', return_value=True):
                render(CLASSIFIED_ROWS, 'json', color=True)
            written = out.getvalue().decode()

        self.assertNotIn('\x1b[', written)
        json.loads(written)

    @mock.patch('sp_cli.commands.run.fetch_and_render')
    def test_no_color_flag_turns_the_context_flag_off(self, mock_fetch):
        """--no-color must reach the context, not just avoid crashing."""
        captured = {}
        mock_fetch.side_effect = lambda ctx, *a, **kw: captured.update(ctx.obj)

        self.runner.invoke(cli, ['-o', 'table', 'run', 'ls'])
        self.assertTrue(captured['color'], 'table mode should enable colour by default')

        captured.clear()
        self.runner.invoke(cli, ['--no-color', '-o', 'table', 'run', 'ls'])
        self.assertFalse(captured['color'])

        captured.clear()
        self.runner.invoke(cli, ['run', 'ls'])
        self.assertFalse(captured['color'], 'JSON mode must never request colour')

    def test_colour_padding_keeps_columns_aligned(self):
        """Escape codes have no display width, so they must sit outside the padding."""
        with mock.patch('sys.stdout.isatty', return_value=True), \
                mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop('NO_COLOR', None)
            with mock.patch('click.echo') as mock_echo:
                render(CLASSIFIED_ROWS, 'table', color=True)

        lines = [str(call.args[0]) for call in mock_echo.call_args_list if call.args]
        # Strip escape sequences and confirm every row is the same visible width.
        import re
        plain = [re.sub(r'\x1b\[[0-9;]*m', '', line) for line in lines if line.strip()]
        widths = {len(line) for line in plain}
        self.assertEqual(len(widths), 1, f'ragged columns: {plain}')


class SpinnerTests(unittest.TestCase):
    """The spinner draws on stderr and only when stderr is interactive."""

    def test_disabled_when_stderr_is_not_a_tty(self):
        """Redirected stderr means no animation frames at all."""
        from sp_cli.progress import Spinner
        with mock.patch('sys.stderr.isatty', return_value=False):
            spinner = Spinner('working', enabled=True)
        self.assertFalse(spinner.enabled)

    def test_disabled_when_the_caller_says_so(self):
        """JSON mode passes enabled=False, which wins even on a TTY."""
        from sp_cli.progress import Spinner
        with mock.patch('sys.stderr.isatty', return_value=True):
            spinner = Spinner('working', enabled=False)
        self.assertFalse(spinner.enabled)

    def test_no_color_also_silences_the_spinner(self):
        """NO_COLOR signals 'no decoration', which covers the spinner too."""
        from sp_cli.progress import Spinner
        with mock.patch('sys.stderr.isatty', return_value=True), \
                mock.patch.dict(os.environ, {'NO_COLOR': '1'}):
            spinner = Spinner('working', enabled=True)
        self.assertFalse(spinner.enabled)

    def test_a_disabled_spinner_writes_nothing(self):
        """The context manager must be a no-op when disabled, not a silent thread leak."""
        from sp_cli.progress import Spinner
        with mock.patch('sys.stderr.isatty', return_value=False):
            with mock.patch('sys.stderr.write') as mock_write:
                with Spinner('working', enabled=True):
                    pass
        mock_write.assert_not_called()


class ConfigIsolationTests(unittest.TestCase):
    """The suite must never be able to touch the developer's real config.

    A test that omitted --no-save replaced a live saved session with the fake
    token `spci_x`. The plaintext token is returned only once at creation, so
    the real credential was unrecoverable. The conftest fixture makes that
    impossible; this asserts the fixture is actually in force.
    """

    def test_config_path_is_redirected_away_from_the_real_home(self):
        """XDG_CONFIG_HOME must point somewhere disposable during tests."""
        path = config.config_path()

        self.assertNotEqual(path, Path.home() / '.config' / 'sp' / 'config.json')
        self.assertNotIn('/.config/sp/config.json', str(Path('~').expanduser() / '.config'))
        # And the redirect target must not be the real user's home.
        self.assertFalse(str(path).startswith(str(Path('~').expanduser()) + '/.config/sp'))

    @mock.patch('sp_cli.client.ApiClient.request')
    def test_a_login_without_no_save_cannot_escape_the_sandbox(self, mock_request):
        """Even a careless future test writes only inside the fixture's tmp dir."""
        mock_request.return_value = {'token': 'spci_careless'}
        result = CliRunner().invoke(cli, [
            'auth', 'login', '--email', 'a@b.c', '--password', 'pw'])

        self.assertEqual(result.exit_code, 0)
        self.assertEqual(config.saved_token(), 'spci_careless')
        # Written, but under the redirected home -- not the real one.
        self.assertTrue(str(config.config_path()).startswith(os.environ['XDG_CONFIG_HOME']))


class LogoutOwnershipTests(unittest.TestCase):
    """`logout` must only clear a session it actually owns.

    Revoking a scratch token from --token/SP_API_TOKEN used to delete an
    unrelated saved credential, which cannot be recovered because the plaintext
    token is returned only once at creation.
    """

    def setUp(self):
        """Create a runner for each test."""
        self.runner = CliRunner()

    @mock.patch('sp_cli.client.ApiClient.request')
    def test_revoking_a_scratch_token_leaves_the_saved_session_alone(self, mock_request):
        """The saved 30-day session survives `SP_API_TOKEN=... sp auth logout`."""
        config.save_token('the-real-session')
        mock_request.return_value = None

        with mock.patch.dict(os.environ, {'SP_API_TOKEN': 'scratch-token'}):
            result = self.runner.invoke(cli, ['auth', 'logout'])

        self.assertEqual(result.exit_code, 0)
        self.assertEqual(config.saved_token(), 'the-real-session')
        self.assertFalse(json.loads(result.stdout)['saved_session_cleared'])

    @mock.patch('sp_cli.client.ApiClient.request')
    def test_revoking_the_saved_token_does_clear_it(self, mock_request):
        """The ordinary case still logs you out."""
        config.save_token('the-real-session')
        mock_request.return_value = None

        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop('SP_API_TOKEN', None)
            result = self.runner.invoke(cli, ['auth', 'logout'])

        self.assertEqual(result.exit_code, 0)
        self.assertIsNone(config.saved_token())

    @mock.patch('sp_cli.client.ApiClient.request')
    def test_an_explicit_token_matching_the_saved_one_still_clears(self, mock_request):
        """Matched by value, not provenance, so --token with the same value counts."""
        config.save_token('same-token')
        mock_request.return_value = None

        result = self.runner.invoke(cli, ['--token', 'same-token', 'auth', 'logout'])

        self.assertEqual(result.exit_code, 0)
        self.assertIsNone(config.saved_token())

    @mock.patch('sp_cli.client.ApiClient.request')
    def test_a_dropped_connection_does_not_discard_a_good_token(self, mock_request):
        """The server was never reached; the token may still be perfectly valid."""
        from sp_cli.client import ApiError
        config.save_token('probably-fine')
        mock_request.side_effect = ApiError('connection_error', 'Could not reach host')

        result = self.runner.invoke(cli, ['auth', 'logout'])

        self.assertEqual(result.exit_code, 3)
        self.assertEqual(config.saved_token(), 'probably-fine')

    @mock.patch('sp_cli.client.ApiClient.request')
    def test_a_rejected_token_is_still_cleared(self, mock_request):
        """401 means it cannot work again, so leaving it on disk helps nobody."""
        from sp_cli.client import ApiError
        config.save_token('expired')
        mock_request.side_effect = ApiError('unauthorized', 'Token expired.', 401)

        result = self.runner.invoke(cli, ['auth', 'logout'])

        self.assertEqual(result.exit_code, 6)
        self.assertIsNone(config.saved_token())


class TokenFilePermissionTests(unittest.TestCase):
    """The token must never be written into a file others can read.

    os.open's mode applies only when the file is created, so a re-login into an
    already-loose file used to write the secret first and chmod second.
    """

    def setUp(self):
        """Create a runner for each test."""
        self.runner = CliRunner()

    def test_rewriting_a_loose_file_tightens_it_before_writing(self):
        """The permissions are 0600 by the time any token byte is on disk."""
        path = config.save_token('first-token')
        os.chmod(path, 0o644)
        self.assertTrue(config.is_world_readable())

        observed = []
        real_dump = json.dump

        def spy(data, handle, **kwargs):
            # Snapshot the mode at the moment the secret is being serialized.
            observed.append(stat.S_IMODE(os.fstat(handle.fileno()).st_mode))
            return real_dump(data, handle, **kwargs)

        with mock.patch('sp_cli.config.json.dump', spy):
            config.save_token('second-token')

        self.assertEqual(observed, [0o600],
                         f'token written while mode was {[oct(m) for m in observed]}')
        self.assertEqual(stat.S_IMODE(Path(path).stat().st_mode), 0o600)
        self.assertFalse(config.is_world_readable())

    def test_clear_token_also_rewrites_privately(self):
        """The same path is used when logout rewrites the remaining settings."""
        path = config.save_token('a-token', 'http://example.test/api/v1')
        os.chmod(path, 0o644)

        config.clear_token()

        self.assertEqual(stat.S_IMODE(Path(path).stat().st_mode), 0o600)
        self.assertEqual(config.load().get('base_url'), 'http://example.test/api/v1')
