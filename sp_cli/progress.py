"""A minimal progress spinner for slow, multi-page calls.

Decoration, so it obeys the same rule as colour: it is suppressed whenever the
output is not a live terminal, whenever ``NO_COLOR`` is set, and always in JSON
mode. It draws on **stderr**, so even when it does run it cannot contaminate the
payload a caller is parsing on stdout.
"""

import itertools
import os
import sys
import threading
from types import TracebackType
from typing import Optional, Type

#: Frames cycle at this interval, in seconds.
_INTERVAL = 0.1

#: Plain ASCII so it renders in a bare terminal without a Unicode font.
_FRAMES = ('|', '/', '-', '\\')


class Spinner:
    """Context manager that animates a label on stderr while work runs."""

    def __init__(self, label: str, enabled: bool = True) -> None:
        """
        Configure the spinner.

        :param label: Text shown beside the animation.
        :type label: str
        :param enabled: Whether the caller wants it; still suppressed when
            stderr is not a TTY or ``NO_COLOR`` is set.
        :type enabled: bool
        """
        self.label = label
        self.enabled = enabled and _spinner_allowed()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def __enter__(self) -> 'Spinner':
        """
        Start animating, if enabled.

        :return: This spinner.
        :rtype: Spinner
        """
        if self.enabled:
            self._thread = threading.Thread(target=self._spin, daemon=True)
            self._thread.start()
        return self

    def __exit__(self, exc_type: Optional[Type[BaseException]],
                 exc: Optional[BaseException],
                 traceback: Optional[TracebackType]) -> None:
        """
        Stop animating and erase the line.

        :param exc_type: Exception class, if the block raised.
        :type exc_type: Optional[Type[BaseException]]
        :param exc: The exception, if the block raised.
        :type exc: Optional[BaseException]
        :param traceback: The traceback, if the block raised.
        :type traceback: Optional[TracebackType]
        """
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._erase()

    def _spin(self) -> None:
        """Draw frames until asked to stop."""
        for frame in itertools.cycle(_FRAMES):
            if self._stop.is_set():
                return
            sys.stderr.write(f'\r{frame} {self.label}')
            sys.stderr.flush()
            if self._stop.wait(_INTERVAL):
                return

    def _erase(self) -> None:
        """Blank the spinner line so it leaves no residue behind."""
        sys.stderr.write('\r' + ' ' * (len(self.label) + 2) + '\r')
        sys.stderr.flush()


def _spinner_allowed() -> bool:
    """
    Report whether a spinner may be drawn at all.

    :return: ``True`` only when stderr is an interactive terminal.
    :rtype: bool
    """
    if os.environ.get('NO_COLOR'):
        return False
    return sys.stderr.isatty()
