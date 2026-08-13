"""Turn a sample's cross-run history into a verdict about *why* it is failing now.

A classification code (from :mod:`sp_cli.classifier`) says how a test failed.
It cannot say whether the failure is news. That needs the sample's history from
``/samples/{id}/history``, which is what this module reads.

The distinction the whole module exists for: a test that passed in the previous
run and fails now is a regression someone introduced, and it is the first thing
worth looking at. A test that has never passed is a known gap. Both show up as
plain ``fail`` in a run, and the platform's own UI does not separate them.
"""

from typing import Any, Dict, List, Optional, Tuple

#: Passed in the previous run, fails now — the failure this run introduced.
NEW_REGRESSION = 'NEW_REGRESSION'
#: Was already failing before this run, but did pass at some point earlier.
STILL_FAILING = 'STILL_FAILING'
#: No run in the window ever passed — a known gap, not this run's doing.
NEVER_PASSED = 'NEVER_PASSED'
#: Flips between pass and fail often enough that a single flip proves nothing.
FLAKY = 'FLAKY'
#: The sample has no prior run in the window to compare against.
NO_HISTORY = 'NO_HISTORY'
#: History could not be read (no sample id, or the lookup failed).
UNKNOWN = 'UNKNOWN'

#: Pass/fail flips among prior runs at or above which a failure reads as flaky
#: rather than as a regression. Two flips means the series already alternates,
#: so this run's flip carries no signal.
FLAKY_TRANSITION_THRESHOLD = 2


def _is_pass(entry: Dict[str, Any]) -> bool:
    """
    Report whether a history entry records a pass.

    :param entry: One entry from ``/samples/{id}/history``.
    :type entry: Dict[str, Any]
    :return: True if the entry's status is ``pass``.
    :rtype: bool
    """
    return entry.get('status') == 'pass'


def split_history(entries: List[Dict[str, Any]], run_id: int,
                  regression_test_id: Optional[int]) -> Tuple[Optional[Dict[str, Any]],
                                                              List[Dict[str, Any]]]:
    """
    Separate the current run's entry from the runs that came before it.

    ``/samples/{id}/history`` covers every regression test defined for the
    sample and includes the run being investigated, so both have to be filtered
    out before the remaining entries mean anything.

    :param entries: History entries as returned by the API (newest first).
    :type entries: List[Dict[str, Any]]
    :param run_id: The run being investigated.
    :type run_id: int
    :param regression_test_id: Restrict to this regression test, when known.
    :type regression_test_id: Optional[int]
    :return: The current run's entry (or None) and the prior entries, newest first.
    :rtype: Tuple[Optional[Dict[str, Any]], List[Dict[str, Any]]]
    """
    if regression_test_id is not None:
        entries = [e for e in entries if e.get('regression_test_id') == regression_test_id]

    current = next((e for e in entries if e.get('run_id') == run_id), None)
    # Run ids increase over time, so "older" is "lower id". Comparing ids rather
    # than tested_at avoids trusting a timestamp that is null for queued runs.
    prior = [e for e in entries if isinstance(e.get('run_id'), int) and e['run_id'] < run_id]
    return current, prior


def _count_transitions(chronological: List[Dict[str, Any]]) -> int:
    """
    Count pass/fail flips across a chronologically ordered history.

    :param chronological: Prior entries, oldest first.
    :type chronological: List[Dict[str, Any]]
    :return: The number of times the outcome changed between runs.
    :rtype: int
    """
    flips = 0
    for previous, current in zip(chronological, chronological[1:]):
        if _is_pass(previous) != _is_pass(current):
            flips += 1
    return flips


def classify_history(current: Optional[Dict[str, Any]],
                     prior: List[Dict[str, Any]],
                     window_truncated: bool = False) -> Dict[str, Any]:
    """
    Decide whether a current failure is new, long-standing, never-working, or noise.

    ``window_truncated`` says the caller could not see as far back as it asked
    for. That only changes NEVER_PASSED: "has never passed" and "has not passed
    within the few runs I could see" are different claims, and the second must
    not be reported with high confidence. The other verdicts are unaffected --
    they are decided by the most recent entries, which are always present.

    :param current: The current run's history entry, if it was found.
    :type current: Optional[Dict[str, Any]]
    :param prior: Entries for earlier runs, newest first.
    :type prior: List[Dict[str, Any]]
    :param window_truncated: Whether older runs existed but could not be read.
    :type window_truncated: bool
    :return: A verdict block with the supporting run ids and signature comparison.
    :rtype: Dict[str, Any]
    """
    signature = current.get('failure_signature') if current else None
    if not prior:
        return {'verdict': NO_HISTORY, 'confidence': 'low',
                'reason': 'No earlier run of this test to compare against',
                'last_pass_run': None, 'previous_run': None, 'prior_runs_considered': 0,
                'transitions': 0, 'signature': signature, 'signature_changed': None,
                'window_truncated': window_truncated}

    previous = prior[0]
    last_pass = next((e for e in prior if _is_pass(e)), None)
    last_fail = next((e for e in prior if not _is_pass(e)), None)
    transitions = _count_transitions(list(reversed(prior)))

    signature_changed: Optional[bool] = None
    if signature is not None and last_fail is not None:
        signature_changed = last_fail.get('failure_signature') != signature

    block = {
        'last_pass_run': last_pass.get('run_id') if last_pass else None,
        'previous_run': previous.get('run_id'),
        'prior_runs_considered': len(prior),
        'transitions': transitions,
        'signature': signature,
        'signature_changed': signature_changed,
        'window_truncated': window_truncated,
    }

    if transitions >= FLAKY_TRANSITION_THRESHOLD:
        block.update(verdict=FLAKY, confidence='medium',
                     reason=(f'Flipped between pass and fail {transitions} times in the last '
                             f'{len(prior)} runs, so this failure may not be real'))
    elif _is_pass(previous):
        block.update(verdict=NEW_REGRESSION, confidence='high',
                     reason=f"Passed in run {previous.get('run_id')}, fails here")
    elif last_pass is None and window_truncated:
        # Older runs exist but could not be read, so a pass may be just beyond
        # the window. Reported as NEVER_PASSED for grouping, but not asserted.
        block.update(verdict=NEVER_PASSED, confidence='low',
                     reason=(f'Has not passed in the {len(prior)} runs visible here, but older '
                             f'runs could not be read, so it may have passed before that'))
    elif last_pass is None:
        block.update(verdict=NEVER_PASSED, confidence='high',
                     reason=f'Has not passed in any of the last {len(prior)} runs')
    else:
        block.update(verdict=STILL_FAILING, confidence='high',
                     reason=(f"Already failing in run {previous.get('run_id')}; "
                             f"last passed in run {last_pass.get('run_id')}"))
    return block


def unknown_history(reason: str) -> Dict[str, Any]:
    """
    Build the verdict block used when history could not be read at all.

    Kept as a real verdict rather than a missing key so every failure row has
    the same shape, which is what makes the JSON output safe to iterate over.

    :param reason: Why the lookup did not happen.
    :type reason: str
    :return: An ``UNKNOWN`` verdict block.
    :rtype: Dict[str, Any]
    """
    return {'verdict': UNKNOWN, 'confidence': 'low', 'reason': reason,
            'last_pass_run': None, 'previous_run': None, 'prior_runs_considered': 0,
            'transitions': 0, 'signature': None, 'signature_changed': None,
            'window_truncated': False}


def group_by_verdict(failures: List[Dict[str, Any]]) -> Dict[str, int]:
    """
    Count classified failures by their history verdict.

    :param failures: Failure rows that each carry a ``history`` block.
    :type failures: List[Dict[str, Any]]
    :return: A mapping of verdict → count, highest first.
    :rtype: Dict[str, int]
    """
    counts: Dict[str, int] = {}
    for failure in failures:
        verdict = failure.get('history', {}).get('verdict', UNKNOWN)
        counts[verdict] = counts.get(verdict, 0) + 1
    return dict(sorted(counts.items(), key=lambda item: item[1], reverse=True))
