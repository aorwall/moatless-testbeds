"""
This file is adapted from SWE-bench:
https://github.com/princeton-nlp/SWE-bench/blob/main/swebench/harness/log_parsers.py

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

import logging
import re

from testbeds.schema import TestResult, TestStatus, TraceItem

logger = logging.getLogger(__name__)


def parse_log(log: str, repo: str) -> list[TestResult]:
    log_parser = MAP_REPO_TO_PARSER[repo]
    test_results = log_parser(log)

    if not test_results:
        logger.info(f"No test results found in log, will check for unhandled errors.")
        # Check for unhandled pytest error
        if detect_unhandled_pytest_error(log):
            logger.info("Found unhandled pytest error in log")
            unhandled_test_result = parse_unhandled_pytest_error(
                log, "unhandled_test_error"
            )
            test_results.append(unhandled_test_result)
        else:
            lines = log.splitlines()
            traceback_start = next(
                (
                    i
                    for i, line in enumerate(lines)
                    if "Traceback (most recent call last):" in line
                ),
                None,
            )
            if traceback_start is not None:
                traceback_end = next(
                    (
                        i
                        for i, line in enumerate(
                            lines[traceback_start:], start=traceback_start
                        )
                        if "During handling of the above exception" in line
                    ),
                    len(lines),
                )
                traceback = "\n".join(lines[traceback_start:traceback_end])
                traceback_result = parse_traceback(traceback)
                if traceback_result:
                    test_results.append(traceback_result)

    # Skip testbed prefix in file paths
    for result in test_results:
        if result.file_path and result.file_path.startswith("/testbed/"):
            result.file_path = result.file_path[len("/testbed/") :]

        if result.failure_output:
            result.failure_output = result.failure_output.replace("/testbed/", "")

    return test_results


def parse_log_pytest(log: str) -> list[TestResult]:
    test_results = []
    test_errors = []

    failure_outputs = {}
    current_failure = None
    current_section = []
    option_pattern = re.compile(r"(.*?)\[(.*)\]")
    escapes = "".join([chr(char) for char in range(1, 32)])

    test_summary_phase = False
    failures_phase = False
    errors_phase = False
    error_patterns = [
        (re.compile(r"ERROR collecting (.*) ___.*"), "collection"),
        (re.compile(r"ERROR at setup of (.*) ___.*"), "setup"),
        (re.compile(r"ERROR at teardown of (.*) ___.*"), "teardown"),
        (re.compile(r"ERROR (.*) ___.*"), "general"),
    ]

    for line in log.split("\n"):
        if "short test summary info" in line:
            test_summary_phase = True
            failures_phase = False
            errors_phase = False
            # Only include results for last test summary for now
            test_results = []
            continue

        if "=== FAILURES ===" in line:
            test_summary_phase = False
            failures_phase = True
            errors_phase = False
            continue

        if "=== ERRORS ===" in line:
            test_summary_phase = False
            failures_phase = True
            errors_phase = True
            continue

        # Remove ANSI codes and escape characters
        line = re.sub(r"\[(\d+)m", "", line)
        line = line.translate(str.maketrans("", "", escapes))

        if (
            not failures_phase
            and any([line.startswith(x.value) for x in TestStatus])
            or any([line.endswith(x.value) for x in TestStatus])
        ):
            if line.startswith(TestStatus.FAILED.value):
                line = line.replace(" - ", " ")

            test_case = line.split()
            if len(test_case) <= 1:
                continue

            if any([line.startswith(x.value) for x in TestStatus]):
                status_str = test_case[0]
            else:
                status_str = test_case[-1]

            if status_str.endswith(":"):
                status_str = status_str[:-1]

            if status_str != "SKIPPED" and "::" not in line:
                continue

            try:
                status = TestStatus(status_str)
            except ValueError:
                logger.exception(f"Unknown status: {status_str} on line {line}")
                status = TestStatus.ERROR

            # Handle SKIPPED cases with [number]
            if (
                status == TestStatus.SKIPPED
                and test_case[1].startswith("[")
                and test_case[1].endswith("]")
            ):
                file_path_with_line = test_case[2]
                file_path, line_number = file_path_with_line.split(":", 1)
                method = None
                full_name = " ".join(test_case[2:])
            else:
                full_name = " ".join(test_case[1:])

                has_option = option_pattern.search(full_name)
                if has_option:
                    main, option = has_option.groups()
                    if (
                        option.startswith("/")
                        and not option.startswith("//")
                        and "*" not in option
                    ):
                        option = "/" + option.split("/")[-1]

                    # In the SWE-Bench dataset only the first word in an option is included for some reason...
                    if option and " " in option:
                        option = option.split()[0]
                        full_name = f"{main}[{option}"
                    else:
                        full_name = f"{main}[{option}]"

                parts = full_name.split("::")
                if len(parts) > 1:
                    file_path = parts[0]
                    method = ".".join(parts[1:])

                    if not has_option:
                        method = method.split()[0]
                else:
                    file_path, method = None, None

            test_results.append(
                TestResult(
                    status=status, name=full_name, file_path=file_path, method=method
                )
            )

        error_match = None
        error_type = None
        for pattern, err_type in error_patterns:
            match = pattern.search(line)
            if match:
                error_match = match
                error_type = err_type
                break

        if error_match:
            if current_failure and current_section:
                failure_outputs[current_failure].extend(current_section)

            if error_match.group(1).endswith(".py"):
                current_failure = (
                    f"{error_type.capitalize()} error in {error_match.group(1)}"
                )
                test_errors.append(
                    TestResult(
                        status=TestStatus.ERROR,
                        name=current_failure,
                        file_path=error_match.group(1),
                    )
                )
                failure_outputs[current_failure] = []
                current_section = []
            else:
                current_failure = error_match.group(1)
                failure_outputs[current_failure] = []
                current_section = []
        elif line.startswith("_____"):
            if current_failure and current_section:
                failure_outputs[current_failure].extend(current_section)
            current_failure = line.strip("_ ")
            failure_outputs[current_failure] = []
            current_section = []
        elif line.startswith("====="):
            if current_failure and current_section:
                failure_outputs[current_failure].extend(current_section)
            current_failure = None
            current_section = []
        elif current_failure:
            current_section.append(line)

    # Add the last section if exists
    if current_failure and current_section:
        failure_outputs[current_failure].extend(current_section)

    test_results.extend(test_errors)

    # Add failure outputs to corresponding failed or error tests
    for test in test_results:
        if test.method in failure_outputs:
            test.failure_output = "\n".join(failure_outputs[test.method])

        if test.name in failure_outputs:
            test.failure_output = "\n".join(failure_outputs[test.name])

    return test_results


# Function to detect unhandled errors that can't be parsed by your regular parser
def detect_unhandled_pytest_error(log: str) -> bool:
    """
    Detects if the log contains unhandled pytest-style errors, excluding those covered by the regular parser.
    """
    # Patterns to match collection, setup, or teardown errors that are unhandled
    unhandled_patterns = [
        r"ImportError while loading conftest",  # Specifically the case you mentioned
        r"Error during collection",
        r"Error while loading fixture",
    ]

    # Search for any unhandled pattern in the log
    for pattern in unhandled_patterns:
        if re.search(pattern, log):
            return True
    return False


# Function to parse the unhandled pytest error into TestResult format
def parse_unhandled_pytest_error(log: str, test_name: str) -> TestResult:
    """
    Parses a pytest-style error log that isn't handled by the regular parser into a TestResult object.
    """
    stacktrace = []
    failure_output = None

    # Extract each line of the stack trace
    trace_lines = log.split("\n")

    # Pattern to match: file_path:line_number: in method_name (method is the last item on the line)
    pattern = r"([^:]+):(\d+):\s+in\s+(.+)$"

    i = 0
    while i < len(trace_lines):
        trace = trace_lines[i]
        # Ensure trace is a string and search for matches
        if isinstance(trace, str):
            match = re.search(pattern, trace)

            if match:
                file_path = match.group(1)
                line_number = int(match.group(2))
                method_name = match.group(3)

                # Now look ahead to the next line for the output
                if i + 1 < len(trace_lines):
                    output = trace_lines[i + 1].strip()  # Get the next line as output
                else:
                    output = ""

                trace_item = TraceItem(
                    file_path=file_path.strip(),
                    line_number=line_number,
                    method=method_name,
                    output=output,
                )
                stacktrace.append(trace_item)

                i += 2
            else:
                i += 1
        else:
            i += 1

    # Extract the final error type and message
    error_message_match = re.search(r"E\s+(\w+Error):\s+(.+)", log)
    if error_message_match:
        failure_output = (
            f"{error_message_match.group(1)}: {error_message_match.group(2)}"
        )
    else:
        failure_output = log

    test_result = TestResult(
        status=TestStatus.ERROR,
        name=test_name,
        failure_output=failure_output,
        stacktrace=stacktrace,
    )

    return test_result


def parse_log_django(log: str) -> list[TestResult]:
    test_status_map = {}

    current_test = None
    current_method = None
    current_file_path = None
    current_traceback_item: TraceItem | None = None
    current_output = []

    test_pattern = re.compile(r"^(\w+) \(([\w.]+)\)")

    lines = log.split("\n")
    for line in lines:
        line = line.strip()

        match = test_pattern.match(line)
        if match:
            current_test = match.group(0)
            method_name = match.group(1)
            full_path = match.group(2).split(".")

            # Extract file path and class name
            file_path_parts = [part for part in full_path[:-1] if part[0].islower()]
            class_name = full_path[-1] if full_path[-1][0].isupper() else None

            current_file_path = "tests/" + "/".join(file_path_parts) + ".py"
            current_method = (
                f"{class_name}.{method_name}" if class_name else method_name
            )

        if current_test:
            if "..." in line:
                swebench_name = line.split("...")[0].strip()
            else:
                swebench_name = None

            if "... ok" in line or line == "ok":
                if swebench_name:
                    current_test = swebench_name
                test_status_map[current_method] = TestResult(
                    status=TestStatus.PASSED,
                    file_path=current_file_path,
                    name=current_test,
                    method=current_method,
                )
                current_test = None
                current_method = None
                current_file_path = None
            elif "FAIL" in line or "\nFAIL" in line:
                if swebench_name:
                    current_test = swebench_name
                test_status_map[current_method] = TestResult(
                    status=TestStatus.FAILED,
                    file_path=current_file_path,
                    name=current_test,
                    method=current_method,
                )
                current_test = None
                current_method = None
                current_file_path = None
            elif "ERROR" in line or "\nERROR" in line:
                if swebench_name:
                    current_test = swebench_name
                test_status_map[current_method] = TestResult(
                    status=TestStatus.ERROR,
                    file_path=current_file_path,
                    name=current_test,
                    method=current_method,
                )
                current_test = None
                current_method = None
                current_file_path = None
            elif " ... skipped" in line or "\nskipped" in line:
                if swebench_name:
                    current_test = swebench_name
                test_status_map[current_method] = TestResult(
                    status=TestStatus.SKIPPED,
                    file_path=current_file_path,
                    name=current_test,
                    method=current_method,
                )
                current_test = None
                current_method = None
                current_file_path = None
            continue

    for line in lines:
        if line.startswith("===================="):
            if current_method and current_output and current_method in test_status_map:
                test_status_map[current_method].failure_output = "\n".join(
                    current_output
                )
            current_method = None
            current_output = []
            current_traceback_item = None
        elif line.startswith("--------------------------") and current_traceback_item:
            if current_method and current_output and current_method in test_status_map:
                test_status_map[current_method].failure_output = "\n".join(
                    current_output
                )

            current_method = None
            current_output = []
            current_traceback_item = None
        elif line.startswith("ERROR: ") or line.startswith("FAIL: "):
            current_test = line.split(": ", 1)[1].strip()
            match = test_pattern.match(current_test)

            if match:
                method_name = match.group(1)
                full_path = match.group(2).split(".")
                class_name = full_path[-1] if full_path[-1][0].isupper() else None
                current_method = (
                    f"{class_name}.{method_name}" if class_name else method_name
                )
            else:
                logger.warning(f"Failed to match test pattern: {current_test}")
                current_method = current_test

        elif len(test_status_map) == 0 and "Traceback (most recent call last)" in line:
            # If traceback is logged but not tests we expect syntax error
            current_method = "traceback"
            test_status_map[current_method] = TestResult(
                status=TestStatus.ERROR, name=current_method, method=current_method
            )

        elif current_method and not line.startswith("--------------------------"):
            current_output.append(line)
            file_path, line_number, method_name = parse_traceback_line(line)
            if file_path:
                if current_traceback_item and current_method in test_status_map:
                    test_status_map[current_method].stacktrace.append(
                        current_traceback_item
                    )

                current_traceback_item = TraceItem(
                    file_path=file_path, line_number=line_number, method=method_name
                )
            elif current_traceback_item:
                if current_traceback_item.output:
                    current_traceback_item.output += "\n"
                current_traceback_item.output += line

    # Handle the last test case
    if current_method and current_output and current_method in test_status_map:
        test_status_map[current_method].failure_output = "\n".join(current_output)
        if current_traceback_item:
            test_status_map[current_method].stacktrace.append(current_traceback_item)
    return list(test_status_map.values())


def parse_traceback_line(line) -> tuple[str, int, str] | tuple[None, None, None]:
    pattern = r'File "([^"]+)", line (\d+), in (\S+)'

    match = re.search(pattern, line)

    if match:
        file_path = match.group(1)
        if file_path.startswith("/testbed/"):
            file_path = file_path.replace("/testbed/", "")
        line_number = int(match.group(2))
        method_name = match.group(3)

        return file_path, line_number, method_name
    else:
        return None, None, None


def parse_traceback(log: str) -> TestResult | None:
    current_trace_item = None
    stacktrace = []

    for line in log.split("\n"):
        file_path, line_number, method_name = parse_traceback_line(line)
        if file_path:
            current_trace_item = TraceItem(
                file_path=file_path, line_number=line_number, method=method_name
            )
            stacktrace.append(current_trace_item)
        elif current_trace_item:
            if current_trace_item.output:
                current_trace_item.output += "\n"
            current_trace_item.output += line

    if not current_trace_item:
        return None

    return TestResult(
        status=TestStatus.ERROR,
        name="traceback",
        method=current_trace_item.method,
        file_path=current_trace_item.file_path,
        failure_output=log,
        stacktrace=stacktrace,
    )


def parse_log_seaborn(log: str) -> list[TestResult]:
    """
    Parser for test logs generated with seaborn testing framework

    Args:
        log (str): log content
    Returns:
        list[TestResult]: List of TestResult objects
    """
    test_results = []
    for line in log.split("\n"):
        if line.startswith(TestStatus.FAILED.value):
            test_case = line.split()[1]
            test_results.append(TestResult(status=TestStatus.FAILED, name=test_case))
        elif f" {TestStatus.PASSED.value} " in line:
            parts = line.split()
            if parts[1] == TestStatus.PASSED.value:
                test_case = parts[0]
                test_results.append(
                    TestResult(status=TestStatus.PASSED, name=test_case)
                )
        elif line.startswith(TestStatus.PASSED.value):
            parts = line.split()
            test_case = parts[1]
            test_results.append(TestResult(status=TestStatus.PASSED, name=test_case))
    return test_results


def parse_log_sympy(log: str) -> list[TestResult]:
    """
    Parser for test logs generated with Sympy framework

    Args:
        log (str): log content
    Returns:
        list[TestResult]: List of TestResult objects
    """
    test_results = {}
    current_file = None

    for line in log.split("\n"):
        line = line.strip()

        # Check for file path
        if ".py[" in line:
            current_file = line.split("[")[0].strip()
            continue

        if line.startswith("test_"):
            split_line = line.split()
            if len(split_line) < 2:
                continue

            test = split_line[0].strip()
            status = split_line[1]

            if status == "E":
                test_results[test] = TestResult(
                    status=TestStatus.ERROR,
                    name=test,
                    method=test,
                    file_path=current_file,
                )
            elif status == "F":
                test_results[test] = TestResult(
                    status=TestStatus.FAILED,
                    name=test,
                    method=test,
                    file_path=current_file,
                )
            elif status == "ok":
                test_results[test] = TestResult(
                    status=TestStatus.PASSED,
                    name=test,
                    method=test,
                    file_path=current_file,
                )
            elif status == "s":
                test_results[test] = TestResult(
                    status=TestStatus.SKIPPED,
                    name=test,
                    method=test,
                    file_path=current_file,
                )

    current_method = None
    current_file = None
    failure_output = []
    for line in log.split("\n"):
        pattern = re.compile(r"(_*) (.*)\.py:(.*) (_*)")
        match = pattern.match(line)
        if match:
            if current_method and current_method in test_results:
                test_results[current_method].failure_output = "\n".join(failure_output)
                test_results[current_method].file_path = current_file

            current_file = f"{match[2]}.py"
            current_method = match[3]
            failure_output = []
            continue

        if "tests finished" in line:
            if current_method and current_method in test_results:
                test_results[current_method].failure_output = "\n".join(failure_output)
                test_results[current_method].file_path = current_file
            break

        failure_output.append(line)

    if current_method and current_method in test_results:
        test_results[current_method].failure_output = "\n".join(failure_output)
        test_results[current_method].file_path = current_file

    return list(test_results.values())


def parse_log_matplotlib(log: str) -> list[TestResult]:
    """
    Parser for test logs generated with PyTest framework

    Args:
        log (str): log content
    Returns:
        list[TestResult]: List of TestResult objects
    """
    test_results = []
    for line in log.split("\n"):
        line = line.replace("MouseButton.LEFT", "1")
        line = line.replace("MouseButton.RIGHT", "3")
        if any([line.startswith(x.value) for x in TestStatus]):
            if line.startswith(TestStatus.FAILED.value):
                line = line.replace(" - ", " ")
            test_case = line.split()
            if len(test_case) <= 1:
                continue
            status = TestStatus(test_case[0])
            test_results.append(TestResult(status=status, name=test_case[1]))
    return test_results


MAP_REPO_TO_PARSER = {
    "astropy/astropy": parse_log_pytest,
    "django/django": parse_log_django,
    "marshmallow-code/marshmallow": parse_log_pytest,
    "matplotlib/matplotlib": parse_log_pytest,
    "mwaskom/seaborn": parse_log_pytest,
    "pallets/flask": parse_log_pytest,
    "psf/requests": parse_log_pytest,
    "pvlib/pvlib-python": parse_log_pytest,
    "pydata/xarray": parse_log_pytest,
    "pydicom/pydicom": parse_log_pytest,
    "pylint-dev/astroid": parse_log_pytest,
    "pylint-dev/pylint": parse_log_pytest,
    "pytest-dev/pytest": parse_log_pytest,
    "pyvista/pyvista": parse_log_pytest,
    "scikit-learn/scikit-learn": parse_log_pytest,
    "sqlfluff/sqlfluff": parse_log_pytest,
    "sphinx-doc/sphinx": parse_log_pytest,
    "sympy/sympy": parse_log_sympy,
}
