from testbed.schema import TestStatus, TraceItem
from testbed.swebench.log_parsers import parse_log


def test_django_1():
    with open("tests/data/django_output_1.txt") as f:
        log = f.read()

    result = parse_log(log, "django/django")

    failed_count = 0
    for r in result:
        print(f"{r.name} {r.status}")
        if r.status == TestStatus.ERROR:
            failed_count += 1
            assert r.failure_output, f"Failed test {r.name} has no failure output"
            assert r.file_path == "tests/model_fields/tests.py"
            assert (
                "RecursionError: maximum recursion depth exceeded while calling a Python object"
                in r.failure_output
            )
            assert "Ran 30 tests in 0.076s" not in r.failure_output

        assert r.file_path
        assert r.method

    assert len(result) == 30
    assert failed_count == 2


def test_django_2():
    with open("tests/data/django_output_2.txt") as f:
        log = f.read()

    result = parse_log(log, "django/django")

    for r in result:
        print(r)

    # Verify that description is used as test name
    test_basic_formset = [
        r for r in result if r.method == "FormsFormsetTestCase.test_basic_formset"
    ]
    assert len(test_basic_formset) == 1
    assert (
        test_basic_formset[0].name
        == "A FormSet constructor takes the same arguments as Form. Create a"
    )

    # Find weirdly formatted test
    test_absolute_max = [
        r for r in result if r.method == "FormsFormsetTestCase.test_absolute_max"
    ]
    assert len(test_absolute_max) == 1
    assert test_absolute_max[0].status == TestStatus.PASSED
    assert (
        test_absolute_max[0].name
        == "test_absolute_max (forms_tests.tests.test_formsets.FormsFormsetTestCase)"
    )

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

    result = parse_log(log, "django/django")

    failures = [r for r in result if r.status == TestStatus.FAILED]
    assert len(failures) == 2

    assert [r.method for r in failures] == [
        "FormsFormsetTestCase.test_more_initial_data",
        "Jinja2FormsFormsetTestCase.test_more_initial_data",
    ]
    assert all(
        r.file_path == "tests/forms_tests/tests/test_formsets.py" for r in failures
    ), f"File path is not correct {[r.file_path for r in failures]}"
    assert all(r.failure_output for r in failures)

    assert len(result) == 157


def test_django_4():
    with open("tests/data/django_output_4.txt") as f:
        log = f.read()

    result = parse_log(log, "django/django")

    failures = [r for r in result if r.status in [TestStatus.FAILED, TestStatus.ERROR]]
    assert len(failures) == 0

    assert len(result) == 344

    skipped = [r for r in result if r.status == TestStatus.SKIPPED]
    assert len(skipped) == 15


def test_django_5():
    with open("tests/data/django_output_5.txt") as f:
        log = f.read()

    result = parse_log(log, "django/django")

    assert len(result) == 1
    assert len(result[0].stacktrace) == 24


def test_django_formatting():
    output = """test_main_module_without_file_is_not_resolved (utils_tests.test_autoreload.TestIterModulesAndFiles) ... ok
test_module_without_spec (utils_tests.test_autoreload.TestIterModulesAndFiles) ... ok
test_path_with_embedded_null_bytes (utils_tests.test_autoreload.TestIterModulesAndFiles) ... test_paths_are_pathlib_instances (utils_tests.test_autoreload.TestIterModulesAndFiles) ... ok
test_weakref_in_sys_module (utils_tests.test_autoreload.TestIterModulesAndFiles)"""

    result = parse_log(output, "django/django")
    print(result)


def test_django_error():
    stacktrace = """Traceback (most recent call last):
  File "/testbed/django/forms/widgets.py", line 80, in <genexpr>
    return mark_safe('\n'.join(chain.from_iterable(getattr(self, 'render_' + name)() for name in MEDIA_TYPES)))
  File "/testbed/django/forms/widgets.py", line 87, in render_js
    ) for path in self._js
  File "/testbed/django/forms/widgets.py", line 76, in _js
    js = self.merge(js, obj)
  File "/testbed/django/forms/widgets.py", line 144, in merge
    all_files = set(list_1 + list_2)
TypeError: can only concatenate list (not "tuple") to list"""

    log = (
        """test_can_delete (admin_inlines.tests.TestInline) ... ERROR

======================================================================
ERROR: test_can_delete (admin_inlines.tests.TestInline)
----------------------------------------------------------------------
"""
        + stacktrace
    )

    result = parse_log(log, "django/django")
    assert len(result) == 1
    assert result[0].status == TestStatus.ERROR
    assert result[0].file_path == "tests/admin_inlines/tests.py"

    assert result[0].stacktrace == [
        TraceItem(
            file_path="django/forms/widgets.py",
            method="<genexpr>",
            line_number=80,
            output="    return mark_safe('\n'.join(chain.from_iterable(getattr(self, 'render_' + name)() for name in MEDIA_TYPES)))",
        ),
        TraceItem(
            file_path="django/forms/widgets.py",
            method="render_js",
            line_number=87,
            output="    ) for path in self._js",
        ),
        TraceItem(
            file_path="django/forms/widgets.py",
            method="_js",
            line_number=76,
            output="    js = self.merge(js, obj)",
        ),
        TraceItem(
            file_path="django/forms/widgets.py",
            method="merge",
            line_number=144,
            output='    all_files = set(list_1 + list_2)\nTypeError: can only concatenate list (not "tuple") to list',
        ),
    ]

    stacktrace = stacktrace.replace("/testbed/", "")
    assert result[0].failure_output == stacktrace


def test_pytest_1():
    with open("tests/data/pytest_output_1.txt") as f:
        log = f.read()

    result = parse_log(log, "pytest-dev/pytest")

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

    result = parse_log(log, "pytest-dev/pytest")
    assert len(result) == 62

    failed_count = 0
    for r in result:
        if r.status == TestStatus.FAILED:
            failed_count += 1
            assert (
                r.failure_output
            ), f"Failed test {r.name} with method {r.method} has no failure output"

        if r.status != TestStatus.SKIPPED:
            assert r.file_path
            assert r.method

    assert failed_count == 3


def test_pytest_3():
    with open("tests/data/pytest_output_3.txt") as f:
        log = f.read()

    result = parse_log(log, "pytest-dev/pytest")

    failed = [
        r
        for r in result
        if r.status == TestStatus.FAILED and r.file_path == "testing/test_mark.py"
    ]
    assert len(failed) == 1
    assert failed[0].failure_output


def test_pytest_4():
    with open("tests/data/pytest_output_4.txt") as f:
        log = f.read()

    result = parse_log(log, "pytest-dev/pytest")
    assert len(result) == 56


def test_pytest_5():
    with open("tests/data/pytest_output_5.txt") as f:
        log = f.read()

    result = parse_log(log, "pytest-dev/pytest")
    failures = [r for r in result if r.status == TestStatus.FAILED]

    for fai in failures:
        print(fai)

    assert len(failures) == 1
    assert failures[0].failure_output


def test_pytest_6():
    with open("tests/data/pytest_output_6.txt") as f:
        log = f.read()

    result = parse_log(log, "pytest-dev/pytest")
    failures = [r for r in result if r.status == TestStatus.FAILED]

    assert len(failures) == 0


def test_pytest_option_with_space_swebench_naming():
    result = parse_log(
        "PASSED testing/test_mark.py::test_marker_expr_eval_failure_handling[NOT internal_err]",
        "pytest-dev/pytest",
    )
    assert 1 == len(result)
    assert (
        "testing/test_mark.py::test_marker_expr_eval_failure_handling[NOT"
        == result[0].name
    )


def test_pytest_matplotlib():
    with open("tests/data/matplotlib_output_1.txt") as f:
        log = f.read()

    result = parse_log(log, "matplotlib/matplotlib")

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

    result = parse_log(log, "matplotlib/matplotlib")

    failed = [r for r in result if r.status == TestStatus.FAILED]
    assert len(failed) == 1
    assert "def test_double_register_builtin_cmap():" in failed[0].failure_output
    assert ">       with pytest.warns(UserWarning):" in failed[0].failure_output
    assert (
        "E       matplotlib._api.deprecation.MatplotlibDeprecationWarning: "
        in failed[0].failure_output
    )
    assert (
        "lib/matplotlib/tests/test_colors.py:150: MatplotlibDeprecationWarning"
        in failed[0].failure_output
    )

    skipped = [r for r in result if r.status == TestStatus.SKIPPED]
    assert len(skipped) == 1
    assert skipped[0].file_path == "lib/matplotlib/testing/compare.py"

    assert len(result) == 253


def test_pytest_seaborn():
    with open("tests/data/seaborn_output_1.txt") as f:
        log = f.read()

    result = parse_log(log, "mwaskom/seaborn")

    assert len(result) == 84


def test_pytest_seaborn_2():
    with open("tests/data/seaborn_output_2.txt") as f:
        log = f.read()

    result = parse_log(log, "mwaskom/seaborn")

    for r in result:
        assert (
            " Attri" not in r.method
        ), f"Method name contains failure output {r.method}"

    failed = [r for r in result if r.status == TestStatus.FAILED]
    assert len(failed) == 48

    assert len(result) == 85


def test_sphinx_1():
    with open("tests/data/sphinx_output_1.txt") as f:
        log = f.read()

    result = parse_log(log, "sphinx-doc/sphinx")

    errored = [r for r in result if r.status == TestStatus.ERROR]
    assert len(errored) == 1
    assert errored[0].failure_output
    assert "ImportError" in errored[0].failure_output


def test_sympy_1():
    with open("tests/data/sympy_output_1.txt") as f:
        log = f.read()

    result = parse_log(log, "sympy/sympy")

    failed = [r for r in result if r.status == TestStatus.FAILED]
    assert len(failed) == 1
    assert failed[0].failure_output

    errored = [r for r in result if r.status == TestStatus.ERROR]
    assert len(errored) == 3
    assert all(r.failure_output for r in errored)

    assert len(result) == 116
    for r in result:
        assert r.file_path
        assert r.method


def test_sympy_2():
    with open("tests/data/sympy_output_2.txt") as f:
        log = f.read()

    result = parse_log(log, "sympy/sympy")

    failed = [r for r in result if r.status == TestStatus.FAILED]
    assert len(failed) == 1
    assert (
        failed[0].failure_output
        == """Traceback (most recent call last):
  File "sympy/sets/tests/test_sets.py", line 24, in test_imageset
    assert (1, r) not in imageset(x, (x, x), S.Reals)
AssertionError
"""
    )

    assert len(result) == 855


def test_traceback():
    with open("tests/data/syntax_error.txt") as f:
        log = f.read()

    result = parse_log(log, "django/django")
    assert len(result) == 1
    result = result[0]

    assert result.status == TestStatus.ERROR
    assert len(result.stacktrace) == 16
    assert result.stacktrace[0].method == "<module>"
    assert result.stacktrace[0].file_path == "./tests/runtests.py"
    assert result.stacktrace[-1].file_path == "django/db/models/fields/__init__.py"
    assert result.stacktrace[-1].line_number == 30
    assert result.stacktrace[-1].method == "<module>"


def test_import_error_traceback():
    log = """ImportError while loading conftest '/testbed/lib/mpl_toolkits/axes_grid1/tests/conftest.py'.
    lib/mpl_toolkits/axes_grid1/__init__.py:3: in <module>
        from .axes_grid import AxesGrid, Grid, ImageGrid
    lib/mpl_toolkits/axes_grid1/axes_grid.py:29: in <module>
        _cbaraxes_class_factory = cbook._make_class_factory(CbarAxesBase, "Cbar{}")
    E   NameError: name 'CbarAxesBase' is not defined
    """
    result = parse_log(log, "matplotlib/matplotlib")
    assert result
    assert len(result) == 1
    assert result[0].status == TestStatus.ERROR
    assert len(result[0].stacktrace) == 2
    assert (
        result[0].stacktrace[0].file_path == "lib/mpl_toolkits/axes_grid1/__init__.py"
    )
    assert result[0].stacktrace[0].line_number == 3
    assert (
        result[0].stacktrace[0].output
        == "from .axes_grid import AxesGrid, Grid, ImageGrid"
    )
    assert result[0].stacktrace[0].method == "<module>"
    assert (
        result[0].stacktrace[1].file_path == "lib/mpl_toolkits/axes_grid1/axes_grid.py"
    )
    assert result[0].stacktrace[1].line_number == 29
    assert result[0].stacktrace[1].method == "<module>"
    assert result[0].failure_output == "NameError: name 'CbarAxesBase' is not defined"


def test_sympy_4():
    with open("tests/data/sympy_output_4.txt") as f:
        log = f.read()

    result = parse_log(log, "sympy/sympy")

    assert len(result) == 1
    assert result[0].status == TestStatus.ERROR
    assert result[0].name == "traceback"
    assert result[0].file_path == "sympy/combinatorics/permutations.py"
    assert result[0].method == "__new__"
    assert "TypeError: object of type 'int' has no len()" in result[0].failure_output

    assert len(result[0].stacktrace) == 7
    assert result[0].stacktrace[0].file_path == "sympy/utilities/runtests.py"
    assert result[0].stacktrace[0].line_number == 1079
    assert result[0].stacktrace[0].method == "test_file"

    assert result[0].stacktrace[-1].file_path == "sympy/combinatorics/permutations.py"
    assert result[0].stacktrace[-1].line_number == 900
    assert result[0].stacktrace[-1].method == "__new__"
