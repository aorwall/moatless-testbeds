import json
import logging
import re
from enum import Enum

from testbed.schema import TestResult, TestStatus


logger = logging.getLogger(__name__)

def parse_log_pytest(log: str) -> list[TestResult]:
    test_results = []
    failure_outputs = {}
    current_failure = None
    current_section = []
    option_pattern = re.compile(r"(.*?)\[(.*)\]")
    escapes = "".join([chr(char) for char in range(1, 32)])

    test_summary_phase = False
    failures_phase = False

    for line in log.split("\n"):

        if "short test summary info" in line:
            test_summary_phase = True
            failures_phase = False
            continue

        if "=== FAILURES ===" in line:
            test_summary_phase = False
            failures_phase = True
            continue

        # Remove ANSI codes and escape characters
        line = re.sub(r"\[(\d+)m", "", line)
        line = line.translate(str.maketrans("", "", escapes))

        if any([line.startswith(x.value) for x in TestStatus]) or any([line.endswith(x.value) for x in TestStatus]):
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
            if status == TestStatus.SKIPPED and test_case[1].startswith("[") and test_case[1].endswith("]"):
                file_path_with_line = test_case[2]
                file_path, line_number = file_path_with_line.split(':', 1)
                method = None
                full_name = " ".join(test_case[2:])
            else:
                full_name = " ".join(test_case[1:])

                has_option = option_pattern.search(full_name)
                if has_option:
                    main, option = has_option.groups()
                    if option.startswith("/") and not option.startswith("//") and "*" not in option:
                        option = "/" + option.split("/")[-1]
                    full_name = f"{main}[{option}]"

                parts = full_name.split("::")
                if len(parts) > 1:
                    file_path = parts[0]
                    method = ".".join(parts[1:])
                    method = method.split()[0]
                else:
                    file_path, method = None, None

            test_results.append(TestResult(
                status=status,
                name=full_name,
                file_path=file_path,
                method=method
            ))

        if failures_phase:
            if line.startswith("_____"):
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

    # Add failure outputs to corresponding failed tests
    for test in test_results:
        if test.status == TestStatus.FAILED:
            if test.method in failure_outputs:
                test.failure_output = "\n".join(failure_outputs[test.method])

    return test_results



def parse_log_django(log: str) -> list[TestResult]:
    test_status_map = {}

    current_test = None
    current_method = None
    current_file_path = None
    expect_status = False
    current_output = []
    current_traceback = []

    test_pattern = re.compile(r'^(\w+) \(([\w.]+)\)')

    lines = log.split("\n")
    for line in lines:
        line = line.strip()

        match = test_pattern.match(line)
        if match:
            current_test = match.group(0)
            method_name = match.group(1)
            full_path = match.group(2).split('.')
            
            # Extract file path and class name
            file_path_parts = [part for part in full_path[:-1] if part[0].islower()]
            class_name = full_path[-1] if full_path[-1][0].isupper() else None
            
            current_file_path = 'tests/' + '/'.join(file_path_parts) + '.py'
            current_method = f"{class_name}.{method_name}" if class_name else method_name

        if current_test:
            if "..." in line:
                swebench_name = line.split("...")[0].strip()
            else:
                swebench_name = None

            if "... ok" in line or line == "ok":
                if swebench_name:
                    current_test = swebench_name
                test_status_map[current_method] = TestResult(status=TestStatus.PASSED, file_path=current_file_path, name=current_test, method=current_method)
                current_test = None
                current_method = None
                current_file_path = None
            elif "FAIL" in line or "\nFAIL" in line:
                if swebench_name:
                    current_test = swebench_name
                test_status_map[current_method] = TestResult(status=TestStatus.FAILED, file_path=current_file_path, name=current_test, method=current_method)
                current_test = None
                current_method = None
                current_file_path = None
            elif "ERROR" in line or "\nERROR" in line:
                if swebench_name:
                    current_test = swebench_name
                test_status_map[current_method] = TestResult(status=TestStatus.ERROR, file_path=current_file_path, name=current_test, method=current_method)
                current_test = None
                current_method = None
                current_file_path = None
            elif " ... skipped" in line or "\nskipped" in line:
                if swebench_name:
                    current_test = swebench_name
                test_status_map[current_method] = TestResult(status=TestStatus.SKIPPED, file_path=current_file_path, name=current_test, method=current_method)
                current_test = None
                current_method = None
                current_file_path = None
            continue

    for line in lines:
        line = line.strip()

        if line.startswith("===================="):
            if current_method and current_output and current_method in test_status_map:
                test_status_map[current_method].failure_output = "\n".join(current_output)
                if current_traceback:
                    file_path = extract_file_path(current_traceback)
                    if file_path and (
                            not test_status_map[current_method].file_path or
                            not file_path.endswith(test_status_map[current_method].file_path)
                    ):
                        logger.warning(
                            f"File path mismatch: {file_path} vs {test_status_map[current_method].file_path}, will use {file_path}")
                        test_status_map[current_method].file_path = file_path
            current_method = None
            current_output = [line]
            current_traceback = []
        elif line.startswith("--------------------------") and current_traceback:
            if current_method and current_output and current_method in test_status_map:
                test_status_map[current_method].failure_output = "\n".join(current_output)
                if current_traceback:
                    file_path = extract_file_path(current_traceback)
                    if file_path and (
                            not test_status_map[current_method].file_path or
                            not file_path.endswith(test_status_map[current_method].file_path)
                    ):
                        logger.warning(
                            f"File path mismatch: {file_path} vs {test_status_map[current_method].file_path}, will use {file_path}")
                        test_status_map[current_method].file_path = file_path
            current_method = None
            current_output = []
            current_traceback = []
        elif line.startswith("ERROR: ") or line.startswith("FAIL: "):
            current_test = line.split(": ", 1)[1].strip()
            match = test_pattern.match(current_test)

            if match:
                method_name = match.group(1)
                full_path = match.group(2).split('.')
                class_name = full_path[-1] if full_path[-1][0].isupper() else None
                current_method = f"{class_name}.{method_name}" if class_name else method_name
            else:
                logger.warning(f"Failed to match test pattern: {current_test}")
                current_method = current_test

            current_output.append(line)
        elif current_method:
            current_output.append(line)
            if line.startswith("File "):
                current_traceback.append(line)

    # Handle the last test case
    if current_method and current_output and current_method in test_status_map:
        test_status_map[current_method].failure_output = "\n".join(current_output)
        if current_traceback:
            file_path = extract_file_path(current_traceback)
            if file_path and (
                    not test_status_map[current_method].file_path or
                    not file_path.endswith(test_status_map[current_method].file_path)
            ):
                logger.warning(f"File path mismatch: {file_path} vs {test_status_map[current_method].file_path}, will use {file_path}")
                test_status_map[current_method].file_path = file_path

    for test in test_status_map.values():
        if test.file_path:
            extracted_path = extract_file_path(test.failure_output.split('\n') if test.failure_output else [])
            if extracted_path and not extracted_path.endswith(test.file_path):
                test.file_path = extracted_path
        else:
            test.file_path = current_file_path

    return list(test_status_map.values())

def extract_file_path(traceback_lines):
    for line in traceback_lines:
        if line.startswith("File "):
            return line.split('"')[1]
    return None



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
                test_results.append(TestResult(status=TestStatus.PASSED, name=test_case))
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
    for line in log.split("\n"):
        line = line.strip()
        if line.startswith("test_"):
            split_line = line.split()
            if split_line[1] == "E":
                test = split_line[0].strip()
                test_results[test] = TestResult(status=TestStatus.ERROR, name=test, method=test)
            if split_line[1] == "F":
                test = split_line[0].strip()
                test_results[test] = TestResult(status=TestStatus.FAILED, name=test, method=test)
            if split_line[1] == "ok":
                test = split_line[0].strip()
                test_results[test] = TestResult(status=TestStatus.PASSED, name=test, method=test)

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
