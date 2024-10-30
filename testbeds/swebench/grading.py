"""
This file is adapted from SWE-bench:
https://github.com/princeton-nlp/SWE-bench/blob/main/swebench/harness/grading.py

MIT License

Copyright (c) 2023 Carlos E Jimenez, John Yang, Alexander Wettig, Shunyu Yao,
Kexin Pei, Ofir Press, Karthik R Narasimhan

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""

from typing import Any

from testbeds.schema import TestResult, TestStatus
from testbeds.swebench.constants import FAIL_TO_PASS, PASS_TO_PASS, ResolvedStatus

IGNORE_TESTS = ["[100%]", "[", "[100%]------------------------------"]


# MARK: Utility functions
def get_file_name_from_lp(x: str) -> str:
    return x.rsplit("/", 1)[-1]


def get_id_from_lp(x: str) -> str:
    return get_file_name_from_lp(x).split(".")[0]


def get_repo_from_lp(x: str) -> str:
    return get_id_from_lp(x).rsplit("-", 1)[0].replace("__", "/")


def test_passed(case: str, sm: dict[str, str]) -> bool:
    return case in sm and sm[case] == TestStatus.PASSED.value


def test_failed(case: str, sm: dict[str, str]) -> bool:
    return case not in sm or any(
        sm[case] == status
        for status in [TestStatus.FAILED.value, TestStatus.ERROR.value]
    )


def get_eval_tests_report(
    eval_results: list[TestResult], gold_results: dict[str, list[str]]
) -> dict[str, dict[str, list[str]]]:
    """
    Create a report based on failure/pass change from gold results to eval results.

    Args:
        eval_results (list[TestResult]): evaluation results as a list of TestResult objects
        gold_results (dict): gold results
        calculate_to_fail (bool): whether to calculate metrics for "x to fail" tests
    Returns:
        report (dict): report of metrics

    Metric Definitions (Gold Result Pair + Eval Result):
    - Fail-Pass (F2P) + P: Success (Resolution)
    - Pass-Pass (P2P) + P: Success (Maintenance)
    - Fail-Pass (F2P) + F: Failure
    - Pass-Pass (P2P) + F: Failure
    """
    # Convert eval_results to a dict for easier lookup
    eval_dict = {result.name: result.status for result in eval_results}

    # Calculate resolution metrics
    f2p_success = []
    f2p_failure = []
    for test_case in gold_results[FAIL_TO_PASS]:
        if test_case in eval_dict and eval_dict[test_case] == TestStatus.PASSED:
            f2p_success.append(test_case)
        else:
            f2p_failure.append(test_case)

    # Calculate maintenance metrics
    p2p_success = []
    p2p_failure = []
    for test_case in gold_results[PASS_TO_PASS]:
        if test_case in eval_dict and eval_dict[test_case] == TestStatus.PASSED:
            p2p_success.append(test_case)
        else:
            p2p_failure.append(test_case)

    # Remove test signatures from the original dataset as they doesn't really make sense and must be a resut of incorrect parsing
    f2p_success = [x for x in f2p_success if x not in IGNORE_TESTS]
    f2p_failure = [x for x in f2p_failure if x not in IGNORE_TESTS]
    p2p_success = [x for x in p2p_success if x not in IGNORE_TESTS]
    p2p_failure = [x for x in p2p_failure if x not in IGNORE_TESTS]

    results = {
        FAIL_TO_PASS: {
            "success": f2p_success,
            "failure": f2p_failure,
        },
        PASS_TO_PASS: {
            "success": p2p_success,
            "failure": p2p_failure,
        },
    }

    return results


def compute_fail_to_pass(report: dict[str, dict[str, Any]]) -> float:
    """
    Compute fail-to-pass metric. Accepts single report as argument.
    """
    total = len(report[FAIL_TO_PASS]["success"]) + len(report[FAIL_TO_PASS]["failure"])
    if total == 0:
        return 1
    return len(report[FAIL_TO_PASS]["success"]) / total


def compute_pass_to_pass(report: dict[str, dict[str, Any]]) -> float:
    """
    Compute pass-to-pass metric. Accepts single report as argument.
    """
    total = len(report[PASS_TO_PASS]["success"]) + len(report[PASS_TO_PASS]["failure"])
    if total == 0:
        # TODO: Don't factor in p2p metrics
        return 1
    return len(report[PASS_TO_PASS]["success"]) / total


def get_resolution_status(report: dict[str, dict[str, Any]]) -> str:
    """
    Determine resolved status of an evaluation instance

    Criteria:
        - If fail-to-pass (Resolution) = 1 and pass-to-pass (Maintenance) = 1 -> FULL
        - If (fail-to-pass (Resolution) < 1 and > 0) and pass-to-pass (Maintenance) = 1 -> PARTIAL
        - Otherwise -> NO
    """
    f2p = compute_fail_to_pass(report)
    p2p = compute_pass_to_pass(report)

    if f2p == 1 and p2p == 1:
        return ResolvedStatus.FULL.value
    elif f2p < 1 and f2p > 0 and p2p == 1:
        return ResolvedStatus.PARTIAL.value
    else:
        return ResolvedStatus.NO.value
