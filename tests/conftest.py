"""Test-wide safety net for the saved login session.

``sp auth login`` writes a bearer token to ``$XDG_CONFIG_HOME/sp/config.json``.
Without this fixture, any test that reaches that code path writes to the
developer's *real* config -- and it did: a test that omitted ``--no-save``
replaced a live session with the fake token ``spci_x``, and because the
plaintext token is returned only once at creation, the real one was
unrecoverable.

Relying on every future test to remember ``--no-save`` is what failed the first
time, so the isolation is applied automatically to every test rather than
opt-in. Tests that need to assert on the file (``tests/test_ux.py``) still
point ``XDG_CONFIG_HOME`` at their own temporary directory; overriding an
already-redirected variable is harmless.
"""

import os

import pytest


@pytest.fixture(autouse=True)
def isolate_config_home(tmp_path, monkeypatch):
    """
    Point ``XDG_CONFIG_HOME`` at a per-test temporary directory.

    :param tmp_path: pytest's per-test temporary directory.
    :type tmp_path: pathlib.Path
    :param monkeypatch: pytest's environment patcher, which restores on teardown.
    :type monkeypatch: pytest.MonkeyPatch
    """
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path / 'config'))
    # HOME too: config_path() falls back to ~/.config when XDG_CONFIG_HOME is
    # unset, and a test that clears the environment would otherwise escape.
    monkeypatch.setenv('HOME', str(tmp_path / 'home'))
    os.makedirs(tmp_path / 'home', exist_ok=True)
