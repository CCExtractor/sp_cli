"""Tests for the run-to-run failure diff behind ``sp run compare``."""

from tests import SESSION_SANDBOX  # noqa: F401  # redirects the saved session; keep first

import unittest

from sp_cli import compare


def sample(test_id, status, exit_code=0, expected_rc=0, outputs=None):
    """Build a RunSample-shaped result for one regression test."""
    return {
        'regression_test_id': test_id,
        'sample_id': test_id,
        'sample_name': f'sample-{test_id}',
        'status': status,
        'exit_code': exit_code,
        'expected_rc': expected_rc,
        'outputs': outputs if outputs is not None else [],
    }


class IndexRunTests(unittest.TestCase):
    """``index_run`` separates "failed" from "produced a result at all"."""

    def test_executed_includes_passing_tests(self):
        """A passing test is not a failure but it did run, which fixed/not_rerun depends on."""
        failures, executed = compare.index_run([sample(1, 'pass'), sample(2, 'fail')])

        self.assertEqual(set(failures), {2})
        self.assertEqual(executed, {1, 2})

    def test_rows_without_a_test_id_are_ignored(self):
        """A result the CLI cannot key on cannot take part in a set diff."""
        failures, executed = compare.index_run([{'status': 'fail'}, sample(3, 'fail')])

        self.assertEqual(set(failures), {3})
        self.assertEqual(executed, {3})


class CompareRunsTests(unittest.TestCase):
    """The buckets are mutually exclusive and mean what they say."""

    def test_new_requires_the_baseline_to_have_passed_it(self):
        """Failing here and passing there is the definition of a regression."""
        result = compare.compare_runs([sample(1, 'fail')], [sample(1, 'pass')])

        self.assertEqual(result['counts']['new'], 1)
        self.assertEqual(result['counts']['no_baseline'], 0)

    def test_a_test_the_baseline_never_ran_is_not_called_new(self):
        """With nothing to compare against, "new" would be an unsupported claim."""
        result = compare.compare_runs([sample(1, 'fail')], [sample(2, 'pass')])

        self.assertEqual(result['counts']['new'], 0)
        self.assertEqual(result['counts']['no_baseline'], 1)

    def test_same_failure_in_both_is_still_failing(self):
        """Identical code on both sides is the standing baseline, not news."""
        result = compare.compare_runs([sample(1, 'fail')], [sample(1, 'fail')])

        self.assertEqual(result['counts']['still_failing'], 1)
        self.assertEqual(result['counts']['new'], 0)

    def test_a_different_code_is_reported_as_changed(self):
        """A diff that became a crash is not "still failing" in any useful sense."""
        result = compare.compare_runs(
            [sample(1, 'fail', exit_code=139, expected_rc=0)],
            [sample(1, 'fail', outputs=[{'status': 'fail'}])])

        self.assertEqual(result['counts']['changed'], 1)
        row = result['changed'][0]
        self.assertEqual(row['now'], 'SEGFAULT')
        self.assertEqual(row['was'], 'OUTPUT_DIFF')

    def test_passing_now_is_fixed(self):
        """Failed there, ran here, passed here."""
        result = compare.compare_runs([sample(1, 'pass')], [sample(1, 'fail')])

        self.assertEqual(result['counts']['fixed'], 1)
        self.assertEqual(result['counts']['not_rerun'], 0)

    def test_a_baseline_failure_with_no_result_here_is_not_fixed(self):
        """The bug this module exists to prevent.

        Skipped samples are absent from /runs/{id}/samples rather than reported
        as not_started -- run 9360 recorded 1 result out of 237. Treating absence
        as success would turn a collapsed run into a clean sweep.
        """
        result = compare.compare_runs([], [sample(1, 'fail'), sample(2, 'fail')])

        self.assertEqual(result['counts']['fixed'], 0)
        self.assertEqual(result['counts']['not_rerun'], 2)

    def test_every_failure_lands_in_exactly_one_bucket(self):
        """The buckets partition the failures; nothing is double-counted or dropped."""
        run = [sample(1, 'fail'), sample(2, 'fail'), sample(3, 'fail', exit_code=139),
               sample(4, 'pass'), sample(5, 'fail')]
        baseline = [sample(1, 'pass'), sample(2, 'fail'), sample(3, 'fail'),
                    sample(4, 'fail'), sample(6, 'fail')]
        result = compare.compare_runs(run, baseline)

        self.assertEqual(result['counts'],
                         {'new': 1, 'changed': 1, 'still_failing': 1,
                          'fixed': 1, 'not_rerun': 1, 'no_baseline': 1})
        run_side = [row for bucket in ('new', 'changed', 'still_failing', 'no_baseline')
                    for row in result[bucket]]
        self.assertEqual(len(run_side), 4, 'every failing test in the run is reported once')

    def test_coverage_counts_only_tests_with_results(self):
        """`compared_tests` is the overlap the verdicts actually rest on."""
        result = compare.compare_runs([sample(1, 'pass'), sample(2, 'fail')],
                                      [sample(2, 'fail'), sample(3, 'pass')])

        self.assertEqual(result['coverage'], {
            'run_tests_with_results': 2,
            'baseline_tests_with_results': 2,
            'compared_tests': 1,
        })


class CoverageWarningTests(unittest.TestCase):
    """Conditions that make a comparison weaker are stated, not silently allowed."""

    def test_a_clean_comparison_warns_about_nothing(self):
        """Same platform, different commits, full results."""
        result = compare.compare_runs([sample(1, 'fail')], [sample(1, 'pass')])
        warnings = compare.coverage_warnings(
            {'platform': 'linux', 'commit_sha': 'a' * 40},
            {'platform': 'linux', 'commit_sha': 'b' * 40}, result)

        self.assertEqual(warnings, [])

    def test_a_cross_platform_comparison_is_flagged(self):
        """Useful on purpose sometimes, so it is a caveat rather than a refusal."""
        result = compare.compare_runs([sample(1, 'fail')], [sample(1, 'pass')])
        warnings = compare.coverage_warnings(
            {'platform': 'linux', 'commit_sha': 'a' * 40},
            {'platform': 'windows', 'commit_sha': 'b' * 40}, result)

        self.assertTrue(any('Platforms differ' in w for w in warnings))

    def test_the_same_commit_on_both_sides_is_flagged(self):
        """Then any difference is the environment, not the change."""
        result = compare.compare_runs([sample(1, 'fail')], [sample(1, 'pass')])
        warnings = compare.coverage_warnings(
            {'platform': 'linux', 'commit_sha': 'a' * 40},
            {'platform': 'linux', 'commit_sha': 'a' * 40}, result)

        self.assertTrue(any('same commit' in w for w in warnings))

    def test_missing_results_are_called_out(self):
        """not_rerun is easy to skim past; say it in words too."""
        result = compare.compare_runs([], [sample(1, 'fail')])
        warnings = compare.coverage_warnings(
            {'platform': 'linux'}, {'platform': 'linux'}, result)

        self.assertTrue(any('not_rerun rather than fixed' in w for w in warnings))
