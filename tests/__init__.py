"""Tests for the sp CLI (sp_cli).

Redirect the saved-session location before any test touches it.

``sp auth login`` writes a bearer token to ``$XDG_CONFIG_HOME/sp/config.json``,
so a test reaching that code path writes to the developer's *real* config. The
plaintext token is returned only at creation, so an overwritten session is
unrecoverable -- and this has now cost a live token twice.

``tests/conftest.py`` already guards this, but only under pytest: conftest is a
pytest mechanism, and the suite is also run with ``unittest`` and ``nose2``,
where the fixture is never loaded and the guard silently does nothing.

Putting the redirect in the package ``__init__`` covers pytest, ``nose2``, and
``python -m unittest discover`` from the repository root. It does *not* cover
``python -m unittest discover -s tests``, which imports test modules top-level
and never imports this package -- which is why every test module imports
``SESSION_SANDBOX`` below explicitly. ``conftest.py`` still narrows this to a
per-test directory under pytest; overriding an already-redirected variable is
harmless.
"""

import atexit
import os
import shutil
import tempfile

#: Throwaway root standing in for the developer's home during tests. Test
#: modules import this name so that importing them runs the redirect below,
#: whichever runner collected them.
SESSION_SANDBOX = tempfile.mkdtemp(prefix='sp-cli-tests-')

# HOME as well as XDG_CONFIG_HOME: config_path() falls back to ~/.config when
# XDG_CONFIG_HOME is unset, so redirecting only the latter leaves a gap.
os.environ['XDG_CONFIG_HOME'] = os.path.join(SESSION_SANDBOX, 'config')
os.environ['HOME'] = os.path.join(SESSION_SANDBOX, 'home')
os.makedirs(os.environ['HOME'], exist_ok=True)

# The CLI reads these through click's envvar= (see main.py), so a developer with
# either exported -- which is exactly what the README suggests for day-to-day use
# -- changes what the tests observe. SP_BASE_URL made a precedence test fail; a
# real SP_API_TOKEN would quietly become test input. Drop both.
for _leaking in ('SP_BASE_URL', 'SP_API_TOKEN'):
    os.environ.pop(_leaking, None)

atexit.register(shutil.rmtree, SESSION_SANDBOX, True)
