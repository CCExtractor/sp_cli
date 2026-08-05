"""Saved login session at ``~/.config/sp/config.json``.

Holds the token produced by ``sp auth login`` so it does not have to be pasted
into ``SP_API_TOKEN`` for every shell. The file is written ``0600`` because it
holds a bearer credential; a token in a world-readable file is a token leak.

Precedence is ``--token`` > ``SP_API_TOKEN`` > this file, so an explicit flag
or an env var always wins over whatever was saved earlier.
"""

import json
import os
import stat
from pathlib import Path
from typing import Any, Dict, Optional

#: Octal mode for the config file and its directory: owner-only.
_FILE_MODE = 0o600
_DIR_MODE = 0o700


def config_path() -> Path:
    """
    Locate the config file, honouring ``XDG_CONFIG_HOME``.

    :return: Absolute path to ``sp/config.json`` under the config home.
    :rtype: Path
    """
    xdg = os.environ.get('XDG_CONFIG_HOME')
    base = Path(xdg) if xdg else Path.home() / '.config'
    return base / 'sp' / 'config.json'


def load() -> Dict[str, Any]:
    """
    Read the saved session, tolerating a missing or corrupt file.

    A malformed file returns empty rather than raising: a bad config should
    degrade to "not logged in", never break every command.

    :return: The saved mapping, or an empty dict.
    :rtype: Dict[str, Any]
    """
    path = config_path()
    try:
        with path.open(encoding='utf-8') as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def saved_token() -> Optional[str]:
    """
    Return the saved bearer token, if one was stored.

    :return: The token, or ``None`` when no usable session is saved.
    :rtype: Optional[str]
    """
    token = load().get('token')
    return token if isinstance(token, str) and token else None


def save_token(token: str, base_url: Optional[str] = None) -> Path:
    """
    Persist a token (and optionally the base URL it belongs to) at mode 0600.

    The file is created with restrictive permissions from the outset rather
    than chmod-ed afterwards, so the secret is never briefly world-readable.

    :param token: The plaintext bearer token to store.
    :type token: str
    :param base_url: The API root the token authenticates against.
    :type base_url: Optional[str]
    :return: The path written.
    :rtype: Path
    """
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True, mode=_DIR_MODE)

    data = load()
    data['token'] = token
    if base_url:
        data['base_url'] = base_url

    # Open through os.open so the mode applies at creation time. An existing
    # file keeps its inode, so re-chmod it too.
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, _FILE_MODE)
    with os.fdopen(descriptor, 'w', encoding='utf-8') as handle:
        json.dump(data, handle, indent=2)
        handle.write('\n')
    os.chmod(path, _FILE_MODE)
    return path


def clear_token() -> bool:
    """
    Remove the saved token, leaving any other settings in place.

    :return: ``True`` if a token was actually removed.
    :rtype: bool
    """
    path = config_path()
    data = load()
    if 'token' not in data:
        return False
    del data['token']

    if not data:
        try:
            path.unlink()
        except OSError:
            return False
        return True

    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, _FILE_MODE)
    with os.fdopen(descriptor, 'w', encoding='utf-8') as handle:
        json.dump(data, handle, indent=2)
        handle.write('\n')
    return True


def is_world_readable() -> bool:
    """
    Report whether the config file is readable by anyone but its owner.

    Used to warn when a token file was created by an older version, or had its
    permissions loosened by hand.

    :return: ``True`` when group or other bits are set.
    :rtype: bool
    """
    try:
        mode = config_path().stat().st_mode
    except OSError:
        return False
    return bool(mode & (stat.S_IRWXG | stat.S_IRWXO))
