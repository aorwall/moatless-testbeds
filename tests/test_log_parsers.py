from testbed.schema import TestStatus
from testbed.swebench.log_parsers import parse_log_pytest, parse_log_django, parse_log_sympy


def test_django_1():
    with open("tests/data/django_output_1.txt") as f:
        log = f.read()

    result = parse_log_django(log)


    failed_count = 0
    for r in result:
        print(f"{r.name} {r.status}")
        if r.status == TestStatus.ERROR:
            failed_count += 1
            assert r.failure_output, f"Failed test {r.name} has no failure output"
            assert r.file_path == "tests/model_fields/tests.py"
            assert "RecursionError: maximum recursion depth exceeded while calling a Python object" in r.failure_output
            assert "Ran 30 tests in 0.076s" not in r.failure_output

        assert r.file_path
        assert r.method

    assert len(result) == 30
    assert failed_count == 2

def test_django_2():
    with open("tests/data/django_output_2.txt") as f:
        log = f.read()

    result = parse_log_django(log)

    for r in result:
        print(r)

    # Verify that description is used as test name
    test_basic_formset = [r for r in result if r.method == "FormsFormsetTestCase.test_basic_formset"]
    assert len(test_basic_formset) == 1
    assert test_basic_formset[0].name == "A FormSet constructor takes the same arguments as Form. Create a"

    # Find weirdly formatted test
    test_absolute_max = [r for r in result if r.method == "FormsFormsetTestCase.test_absolute_max"]
    assert len(test_absolute_max) == 1
    assert test_absolute_max[0].status == TestStatus.PASSED
    assert test_absolute_max[0].name == "test_absolute_max (forms_tests.tests.test_formsets.FormsFormsetTestCase)"

    failures = [r for r in result if r.status == TestStatus.FAILED]
    assert len(failures) == 2

    for r in result:
        if r.status == TestStatus.FAILED:
            assert r.failure_output, f"Failed test {r.name} has no failure output"
            assert r.file_path == "tests/forms_tests/tests/test_formsets.py"

        assert r.file_path
        assert r.method

    assert len(result) == 157

def test_django_3():
    with open("tests/data/django_output_3.txt") as f:
        log = f.read()

    result = parse_log_django(log)

    failures = [r for r in result if r.status == TestStatus.FAILED]
    assert len(failures) == 2

    assert [r.method for r in failures] == ["FormsFormsetTestCase.test_more_initial_data", "Jinja2FormsFormsetTestCase.test_more_initial_data"]
    assert all(r.file_path == "tests/forms_tests/tests/test_formsets.py" for r in failures), f"File path is not correct {[r.file_path for r in failures]}"
    assert all(r.failure_output for r in failures)

    assert len(result) == 157

def test_django_4():
    with open("tests/data/django_output_4.txt") as f:
        log = f.read()

    result = parse_log_django(log)

    failures = [r for r in result if r.status in [TestStatus.FAILED, TestStatus.ERROR]]
    assert len(failures) == 0

    assert len(result) == 344

    skipped = [r for r in result if r.status == TestStatus.SKIPPED]
    assert len(skipped) == 15



def test_pytest_1():
    with open("tests/data/pytest_output_1.txt") as f:
        log = f.read()

    result = parse_log_pytest(log)

    assert len(result) == 11

    failed_count = 0
    for r in result:
        if r.status == TestStatus.FAILED:
            failed_count += 1
            assert r.failure_output, f"Failed test {r.name} has no failure output"

        assert r.file_path
        assert r.method
        assert r.method in r.name

    assert failed_count == 5

def test_pytest_2():
    with open("tests/data/pytest_output_2.txt") as f:
        log = f.read()

    result = parse_log_pytest(log)
    assert len(result) == 62

    failed_count = 0
    for r in result:
        if r.status == TestStatus.FAILED:
            failed_count += 1
            assert r.failure_output, f"Failed test {r.name} with method {r.method} has no failure output"

        if r.status != TestStatus.SKIPPED:
            assert r.file_path
            assert r.method

    assert failed_count == 3

def test_pytest_4():
    with open("tests/data/pytest_output_4.txt") as f:
        log = f.read()

    result = parse_log_pytest(log)
    assert len(result) == 56

def test_pytest_3():
    with open("tests/data/pytest_output_3.txt") as f:
        log = f.read()

    result = parse_log_pytest(log)

    failed = [r for r in result if r.status == TestStatus.FAILED and r.file_path == "testing/test_mark.py"]
    assert len(failed) == 1
    assert failed[0].failure_output

def test_pytest_matplotlib():
    with open("tests/data/matplotlib_output_1.txt") as f:
        log = f.read()

    result = parse_log_pytest(log)

    assert len(result) == 48

    failed_count = 0
    for r in result:
        if r.status == TestStatus.FAILED:
            failed_count += 1
            assert r.failure_output, f"Failed test {r.name} has no failure output"

        assert r.file_path
        assert r.method
        assert r.method in r.name

    assert failed_count == 2

def test_pytest_matplotlib_2():
    with open("tests/data/matplotlib_output_2.txt") as f:
        log = f.read()

    result = parse_log_pytest(log)

    failed = [r for r in result if r.status == TestStatus.FAILED]
    assert len(failed) == 1
    assert "def test_double_register_builtin_cmap():" in failed[0].failure_output
    assert ">       with pytest.warns(UserWarning):" in failed[0].failure_output
    assert "E       matplotlib._api.deprecation.MatplotlibDeprecationWarning: " in failed[0].failure_output
    assert "lib/matplotlib/tests/test_colors.py:150: MatplotlibDeprecationWarning" in failed[0].failure_output

    skipped = [r for r in result if r.status == TestStatus.SKIPPED]
    assert len(skipped) == 1
    assert skipped[0].file_path == "lib/matplotlib/testing/compare.py"

    assert len(result) == 253



def test_pytest_seaborn():
    with open("tests/data/seaborn_output_1.txt") as f:
        log = f.read()

    result = parse_log_pytest(log)

    assert len(result) == 84


def test_pytest_seaborn_2():
    with open("tests/data/seaborn_output_2.txt") as f:
        log = f.read()

    result = parse_log_pytest(log)

    for r in result:
        assert " Attri" not in r.method, f"Method name contains failure output {r.method}"

    failed = [r for r in result if r.status == TestStatus.FAILED]
    assert len(failed) == 48

    assert len(result) == 85


def test_sympy():
    with open("tests/data/sympy_output_1.txt") as f:
        log = f.read()

    result = parse_log_sympy(log)

    failed = [r for r in result if r.status == TestStatus.FAILED]
    assert len(failed) == 1
    assert failed[0].failure_output

    errored = [r for r in result if r.status == TestStatus.ERROR]
    assert len(errored) == 3
    assert all(r.failure_output for r in errored)

    assert len(result) == 116

