"""Set-diff two runs' failures: what is new, what was fixed, what persists.

The classifier answers "why did this fail". Reviewing a pull request needs the
other question — "was it already failing?" — and that one is comparative. A run
on its own cannot answer it; ``sp investigate --with-history`` tries, but it
depends on ``/samples/{id}/history``, which is unusable against production
(sample-platform#1161). Comparing against a baseline run you name yourself needs
no history endpoint at all.

The trap this module exists to avoid: a test **absent** from a run has not been
fixed. Skipped samples are omitted from ``/runs/{id}/samples`` entirely rather
than reported as ``not_started`` — run 9360 recorded 1 result out of 237 — so
"failing in the baseline, missing here" means *not re-run*, not *repaired*.
Reporting those as fixed would turn a collapsed run into good news.
"""

from typing import Any, Dict, List, Optional, Set, Tuple

from sp_cli.triage import classify_sample, is_failure


def index_run(samples: List[Dict[str, Any]]) -> Tuple[Dict[int, Dict[str, Any]], Set[int]]:
    """
    Index one run's results by regression test id.

    :param samples: ``RunSample`` objects from ``/runs/{id}/samples``.
    :type samples: List[Dict[str, Any]]
    :return: A mapping of test id → classified failure row, and the set of test
        ids that actually produced a result (whatever the outcome).
    :rtype: Tuple[Dict[int, Dict[str, Any]], Set[int]]
    """
    failures: Dict[int, Dict[str, Any]] = {}
    executed: Set[int] = set()
    for sample in samples:
        test_id = sample.get('regression_test_id')
        if not isinstance(test_id, int):
            continue
        executed.add(test_id)
        if is_failure(sample):
            failures[test_id] = classify_sample(sample)
    return failures, executed


def compare_runs(run_samples: List[Dict[str, Any]],
                 baseline_samples: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Diff a run's failures against a baseline run's.

    Every failing test lands in exactly one bucket:

    ``new``
        Fails here, ran and passed in the baseline. The regressions.
    ``changed``
        Fails in both, but with a different classification — a diff that became
        a segfault is not "still failing" in any useful sense.
    ``still_failing``
        Fails in both with the same code. Usually the standing baseline.
    ``fixed``
        Failed in the baseline, ran here, and passed.
    ``not_rerun``
        Failed in the baseline and produced no result here at all. Explicitly
        *not* ``fixed``: the evidence is missing, not good.
    ``no_baseline``
        Fails here and the baseline never ran this test, so there is nothing to
        compare against — a test added since, or a baseline that stopped early.

    :param run_samples: Results for the run under review.
    :type run_samples: List[Dict[str, Any]]
    :param baseline_samples: Results for the run to compare against.
    :type baseline_samples: List[Dict[str, Any]]
    :return: The buckets, their counts, and coverage figures.
    :rtype: Dict[str, Any]
    """
    run_fail, run_ran = index_run(run_samples)
    base_fail, base_ran = index_run(baseline_samples)

    new: List[Dict[str, Any]] = []
    changed: List[Dict[str, Any]] = []
    still: List[Dict[str, Any]] = []
    no_baseline: List[Dict[str, Any]] = []

    for test_id, row in sorted(run_fail.items()):
        was = base_fail.get(test_id)
        if was is not None:
            if was['code'] != row['code']:
                changed.append({**row, 'was': was['code'], 'now': row['code']})
            else:
                still.append(row)
        elif test_id in base_ran:
            new.append(row)
        else:
            no_baseline.append(row)

    fixed = [row for test_id, row in sorted(base_fail.items())
             if test_id not in run_fail and test_id in run_ran]
    not_rerun = [row for test_id, row in sorted(base_fail.items())
                 if test_id not in run_ran]

    return {
        'counts': {
            'new': len(new), 'changed': len(changed), 'still_failing': len(still),
            'fixed': len(fixed), 'not_rerun': len(not_rerun), 'no_baseline': len(no_baseline),
        },
        'new': new,
        'changed': changed,
        'fixed': fixed,
        'not_rerun': not_rerun,
        'no_baseline': no_baseline,
        'still_failing': still,
        'coverage': {
            'run_tests_with_results': len(run_ran),
            'baseline_tests_with_results': len(base_ran),
            'compared_tests': len(run_ran & base_ran),
        },
    }


def coverage_warnings(run: Dict[str, Any], baseline: Dict[str, Any],
                      result: Dict[str, Any]) -> List[str]:
    """
    Flag the conditions that make a comparison less trustworthy than it looks.

    None of these are errors — a cross-platform diff is sometimes exactly what
    you want, as when the same commit behaves differently on linux and windows.
    They are stated so a reader does not have to notice for themselves.

    :param run: The run detail object for the run under review.
    :type run: Dict[str, Any]
    :param baseline: The run detail object for the baseline.
    :type baseline: Dict[str, Any]
    :param result: The output of :func:`compare_runs`.
    :type result: Dict[str, Any]
    :return: Human-readable warnings, empty when the comparison is clean.
    :rtype: List[str]
    """
    warnings: List[str] = []
    run_platform: Optional[str] = run.get('platform')
    base_platform: Optional[str] = baseline.get('platform')
    if run_platform != base_platform:
        warnings.append(
            f'Platforms differ ({run_platform} vs {base_platform}); '
            'differences may be platform behaviour rather than change over time.')
    if run.get('commit_sha') and run.get('commit_sha') == baseline.get('commit_sha'):
        warnings.append(
            'Both runs are the same commit, so anything that differs is the '
            'environment rather than the code.')

    coverage = result['coverage']
    for label, key in (('run', 'run_tests_with_results'),
                       ('baseline', 'baseline_tests_with_results')):
        if coverage[key] < coverage['compared_tests'] or coverage[key] == 0:
            warnings.append(f'The {label} recorded no results to compare.')
    if result['counts']['not_rerun']:
        warnings.append(
            f"{result['counts']['not_rerun']} baseline failure(s) produced no result here; "
            'they are reported as not_rerun rather than fixed.')
    return warnings


def pick_references(run: Dict[str, Any],
                    branch_runs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Choose which earlier runs a run is worth being described against.

    Two references answer different questions. The newest run on the target
    branch says whether a failure is broken where everyone else is working. The
    newest one that predates this run is the closest thing to where the branch
    was cut from, which is what separates "this change did it" from "it was
    already like that".

    This is a *proxy* for ancestry, not ancestry: the API exposes no commit
    graph, so a run that predates this one is assumed to precede it in history.
    That holds for a branch cut from the target and stops holding for one cut
    weeks ago and rebased since. The label says "before this run" rather than
    "ancestor" so a reader is not told more than was checked.

    :param run: The run being reported on.
    :type run: Dict[str, Any]
    :param branch_runs: Candidate runs on the target branch, newest first.
    :type branch_runs: List[Dict[str, Any]]
    :return: References as {label, run}, nearest question first, deduplicated.
    :rtype: List[Dict[str, Any]]
    """
    usable = []
    for candidate in branch_runs:
        if candidate.get('run_id') == run.get('run_id'):
            continue
        if candidate.get('platform') != run.get('platform'):
            continue
        # A run that never reached a verdict has nothing to say about this one.
        if candidate.get('status') not in ('pass', 'fail'):
            continue
        usable.append(candidate)
    if not usable:
        return []

    created = run.get('created_at') or ''
    earlier = []
    if created:
        for candidate in usable:
            if (candidate.get('created_at') or '') < created:
                earlier.append(candidate)

    chosen: List[Tuple[str, Dict[str, Any]]] = [('the newest run on the target branch', usable[0])]
    if earlier:
        chosen.append(('the newest run before this one', earlier[0]))

    references: List[Dict[str, Any]] = []
    seen: Set[int] = set()
    for label, candidate in chosen:
        if candidate['run_id'] in seen:
            continue
        seen.add(candidate['run_id'])
        references.append({'label': label, 'run': candidate})
    return references
