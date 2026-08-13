"""Tests for the cross-run history verdicts behind ``sp investigate --with-history``."""

import unittest

from sp_cli.history import (FLAKY, NEVER_PASSED, NEW_REGRESSION, NO_HISTORY,
                            STILL_FAILING, UNKNOWN, classify_history,
                            group_by_verdict, split_history, unknown_history)
from tests import SESSION_SANDBOX  # noqa: F401


def entry(run_id, status, regression_test_id=137, signature=None):
    """
    Build one history entry shaped like ``/samples/{id}/history`` returns.

    :param run_id: The run the entry belongs to.
    :type run_id: int
    :param status: Derived per-sample status for that run.
    :type status: str
    :param regression_test_id: Which regression test the entry is for.
    :type regression_test_id: int
    :param signature: Optional failure signature.
    :type signature: Optional[str]
    :return: A history entry.
    :rtype: dict
    """
    return {'run_id': run_id, 'regression_test_id': regression_test_id, 'status': status,
            'platform': 'windows', 'branch': 'master', 'commit_sha': 'abc1234',
            'tested_at': '2026-07-30T10:00:00Z', 'failure_signature': signature}


class SplitHistoryTests(unittest.TestCase):
    """The endpoint returns more than the run and test being investigated."""

    def test_drops_other_regression_tests_for_the_same_sample(self):
        """One sample can back several regression tests; only the failing one counts."""
        entries = [entry(9299, 'fail'), entry(9298, 'pass'),
                   entry(9298, 'fail', regression_test_id=999)]
        current, prior = split_history(entries, 9299, 137)

        self.assertEqual(current['run_id'], 9299)
        self.assertEqual([e['run_id'] for e in prior], [9298])
        self.assertTrue(all(e['regression_test_id'] == 137 for e in prior))

    def test_excludes_the_run_being_investigated_from_prior(self):
        """The current run appears in its own history and must not count as prior."""
        entries = [entry(9299, 'fail'), entry(9298, 'pass'), entry(9290, 'pass')]
        current, prior = split_history(entries, 9299, 137)

        self.assertEqual(current['run_id'], 9299)
        self.assertEqual([e['run_id'] for e in prior], [9298, 9290])

    def test_ignores_runs_newer_than_the_one_investigated(self):
        """Investigating an older run must not be judged against runs that came after it."""
        entries = [entry(9300, 'pass'), entry(9299, 'fail'), entry(9298, 'pass')]
        _, prior = split_history(entries, 9299, 137)

        self.assertEqual([e['run_id'] for e in prior], [9298])

    def test_keeps_every_entry_when_no_regression_test_is_known(self):
        """Without a regression test id there is nothing to narrow by."""
        entries = [entry(9299, 'fail'), entry(9298, 'pass', regression_test_id=999)]
        _, prior = split_history(entries, 9299, None)

        self.assertEqual(len(prior), 1)


class ClassifyHistoryTests(unittest.TestCase):
    """The verdict is what separates 'someone broke this' from 'this never worked'."""

    def test_no_prior_runs(self):
        """A sample with no earlier run cannot be called a regression."""
        block = classify_history(entry(9299, 'fail'), [])

        self.assertEqual(block['verdict'], NO_HISTORY)
        self.assertEqual(block['prior_runs_considered'], 0)
        self.assertIsNone(block['signature_changed'])

    def test_new_regression_when_the_previous_run_passed(self):
        """Passed last run, fails now — the case worth looking at first."""
        block = classify_history(entry(9299, 'fail'),
                                 [entry(9298, 'pass'), entry(9290, 'pass')])

        self.assertEqual(block['verdict'], NEW_REGRESSION)
        self.assertEqual(block['confidence'], 'high')
        self.assertEqual(block['previous_run'], 9298)
        self.assertEqual(block['last_pass_run'], 9298)

    def test_never_passed_when_no_prior_run_passed(self):
        """A known gap, not something this run introduced."""
        block = classify_history(entry(9299, 'fail'),
                                 [entry(9298, 'fail'), entry(9290, 'missing_output')])

        self.assertEqual(block['verdict'], NEVER_PASSED)
        self.assertIsNone(block['last_pass_run'])

    def test_still_failing_when_it_broke_before_this_run(self):
        """Already broken on arrival, but it did work at some point."""
        block = classify_history(entry(9299, 'fail'),
                                 [entry(9298, 'fail'), entry(9290, 'pass')])

        self.assertEqual(block['verdict'], STILL_FAILING)
        self.assertEqual(block['last_pass_run'], 9290)
        self.assertEqual(block['previous_run'], 9298)

    def test_flaky_beats_regression_when_the_series_alternates(self):
        """A pass/fail flip proves nothing in a series that already flips."""
        block = classify_history(entry(9299, 'fail'),
                                 [entry(9298, 'pass'), entry(9297, 'fail'),
                                  entry(9296, 'pass')])

        self.assertEqual(block['verdict'], FLAKY)
        self.assertEqual(block['transitions'], 2)

    def test_single_flip_is_still_a_regression(self):
        """One transition is the regression case, not the flaky one."""
        block = classify_history(entry(9299, 'fail'),
                                 [entry(9298, 'pass'), entry(9297, 'pass'),
                                  entry(9296, 'fail')])

        self.assertEqual(block['transitions'], 1)
        self.assertEqual(block['verdict'], NEW_REGRESSION)

    def test_signature_change_is_reported_against_the_last_failure(self):
        """Same code, different signature means a different underlying failure."""
        changed = classify_history(
            entry(9299, 'fail', signature='exit_code_mismatch:rc:10'),
            [entry(9298, 'fail', signature='missing_output')])
        unchanged = classify_history(
            entry(9299, 'fail', signature='missing_output'),
            [entry(9298, 'fail', signature='missing_output')])

        self.assertTrue(changed['signature_changed'])
        self.assertFalse(unchanged['signature_changed'])

    def test_signature_unknown_when_the_current_entry_is_missing(self):
        """History may not include the current run; the verdict still works."""
        block = classify_history(None, [entry(9298, 'pass')])

        self.assertEqual(block['verdict'], NEW_REGRESSION)
        self.assertIsNone(block['signature'])
        self.assertIsNone(block['signature_changed'])


class VerdictGroupingTests(unittest.TestCase):
    """The tally is what makes a 40-failure run readable."""

    def test_group_by_verdict_counts_highest_first(self):
        """Counts sort descending so the dominant verdict leads."""
        failures = [
            {'history': {'verdict': NEVER_PASSED}},
            {'history': {'verdict': NEVER_PASSED}},
            {'history': {'verdict': NEW_REGRESSION}},
        ]

        self.assertEqual(group_by_verdict(failures),
                         {NEVER_PASSED: 2, NEW_REGRESSION: 1})

    def test_rows_without_history_count_as_unknown(self):
        """Every row is counted, so the tally always sums to the failure count."""
        self.assertEqual(group_by_verdict([{}, {'history': {}}]), {UNKNOWN: 2})

    def test_unknown_history_has_the_same_shape_as_a_real_verdict(self):
        """Uniform keys are what make the JSON safe for an agent to iterate."""
        real = classify_history(entry(9299, 'fail'), [entry(9298, 'pass')])

        self.assertEqual(set(unknown_history('no id')), set(real))


class TruncatedWindowTests(unittest.TestCase):
    """NEVER_PASSED must not be asserted confidently on a window we could not see past.

    /samples/{id}/history returns entries for every regression test on the
    sample and slices only afterwards, so the window for one test is roughly
    the page size divided by how many tests share the sample. A test that
    passed nine runs ago looked like it had never passed at all.
    """

    @staticmethod
    def _fails(*run_ids):
        """Build failing prior entries, newest first."""
        return [{'run_id': r, 'status': 'fail', 'regression_test_id': 1} for r in run_ids]

    def test_a_short_window_downgrades_confidence(self):
        """Same verdict for grouping, but not claimed as fact."""
        block = classify_history(None, self._fails(98, 97), window_truncated=True)

        self.assertEqual(block['verdict'], NEVER_PASSED)
        self.assertEqual(block['confidence'], 'low')
        self.assertTrue(block['window_truncated'])
        self.assertIn('older runs could not be read', block['reason'])

    def test_a_complete_window_still_asserts_it(self):
        """When the window really is the whole history, the claim stands."""
        block = classify_history(None, self._fails(98, 97), window_truncated=False)

        self.assertEqual(block['verdict'], NEVER_PASSED)
        self.assertEqual(block['confidence'], 'high')
        self.assertFalse(block['window_truncated'])

    def test_truncation_does_not_weaken_the_other_verdicts(self):
        """NEW_REGRESSION and STILL_FAILING are decided by the newest entries, always present."""
        passed_then_failed = [{'run_id': 98, 'status': 'pass', 'regression_test_id': 1}]
        new = classify_history(None, passed_then_failed, window_truncated=True)
        self.assertEqual(new['verdict'], NEW_REGRESSION)
        self.assertEqual(new['confidence'], 'high')

        with_a_pass = self._fails(98) + [{'run_id': 97, 'status': 'pass',
                                          'regression_test_id': 1}]
        still = classify_history(None, with_a_pass, window_truncated=True)
        self.assertEqual(still['verdict'], STILL_FAILING)
        self.assertEqual(still['confidence'], 'high')

    def test_every_verdict_block_carries_the_flag(self):
        """Uniform shape keeps the JSON safe to iterate over."""
        blocks = [
            classify_history(None, [], window_truncated=True),
            classify_history(None, self._fails(98), window_truncated=True),
            unknown_history('lookup failed'),
        ]
        for block in blocks:
            self.assertIn('window_truncated', block, block['verdict'])
