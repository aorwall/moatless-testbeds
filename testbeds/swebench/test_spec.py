"""
This file is adapted from SWE-bench:
https://github.com/princeton-nlp/SWE-bench/blob/main/swebench/harness/test_spec.py

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

import hashlib
import logging
import platform
import re
from dataclasses import dataclass
from typing import Optional

from testbeds.schema import SWEbenchInstance, TestsStatus, EvalTestResult
from testbeds.swebench.constants import (
    MAP_REPO_TO_INSTALL,
    MAP_REPO_VERSION_TO_SPECS,
    USE_X86,
    NON_TEST_EXTS,
    APPLY_PATCH_FAIL,
    APPLY_PATCH_PASS,
    KEY_INSTANCE_ID,
    FAIL_TO_PASS,
    PASS_TO_PASS,
    RUN_TESTS,
)
from testbeds.swebench.grading import get_eval_tests_report, get_resolution_status
from testbeds.swebench.log_parsers import parse_log

DIFF_MODIFIED_FILE_REGEX = r"--- a/(.*)"


logger = logging.getLogger(__name__)


@dataclass
class TestSpec:
    """
    A dataclass that represents a test specification for a single instance of SWE-bench.
    """

    instance_id: str
    repo: str
    version: str
    base_commit: str
    test_patch: str
    fail_to_pass: list[str]
    pass_to_pass: list[str]
    arch: str

    def __post_init__(self):
        self.env_name = "testbed"
        self.repo_directory = f"/{self.env_name}"
        self.specs = MAP_REPO_VERSION_TO_SPECS[self.repo][self.version]

    @classmethod
    def from_instance(cls, instance: SWEbenchInstance) -> "TestSpec":
        assert instance, "Instance is required"
        if isinstance(instance, cls):
            return instance

        if platform.machine() in {"aarch64", "arm64"}:
            arch = "arm64" if instance.instance_id not in USE_X86 else "x86_64"
        else:
            arch = "x86_64"

        return cls(
            instance_id=instance.instance_id,
            repo=instance.repo,
            version=instance.version,
            base_commit=instance.base_commit,
            test_patch=instance.test_patch,
            fail_to_pass=instance.fail_to_pass,
            pass_to_pass=instance.pass_to_pass,
            arch=arch,
        )

    @property
    def eval_script(self):
        return (
            "\n".join(["#!/bin/bash", "set -uxo pipefail"] + self.eval_script_list)
            + "\n"
        )

    @property
    def install_repo_script(self):
        return (
            "\n".join(["#!/bin/bash", "set -euxo pipefail"] + self.repo_script_list)
            + "\n"
        )

    @property
    def base_image_key(self):
        return f"sweb.base.{self.arch}:latest"

    @property
    def env_image_key(self):
        """
        The key for the environment image is based on the hash of the environment script list.
        If the environment script list changes, the image will be rebuilt automatically.

        Note that old images are not automatically deleted, so consider cleaning up old images periodically.
        """
        hash_object = hashlib.sha256()
        hash_object.update(str(self.env_script_list).encode("utf-8"))
        hash_value = hash_object.hexdigest()
        val = hash_value[:22]  # 22 characters is still very likely to be unique
        return f"sweb.env.{self.arch}.{val}:latest"

    @property
    def instance_image_key(self):
        return f"sweb.eval.{self.arch}.{self.instance_id}:latest"

    def get_instance_container_name(self, run_id=None):
        if not run_id:
            return f"sweb.eval.{self.instance_id}"
        return f"sweb.eval.{self.instance_id}.{run_id}"

    @property
    def platform(self):
        if self.arch == "x86_64":
            return "linux/x86_64"
        elif self.arch == "arm64":
            return "linux/arm64/v8"
        else:
            raise ValueError(f"Invalid architecture: {self.arch}")

    @property
    def reset_commands(self):
        return [
            "git clean -fd",
            f"git reset --hard {self.base_commit}",
        ]

    def patch_commands(self, patch_filepath: str) -> list[str]:
        return [
            f"git apply -v {patch_filepath}",
            f"if [ $? -ne 0 ]; then",
            f"    echo 'Failed to apply patch with git apply -v'",
            f"    echo 'Trying again with patch command...'",
            f"    patch --batch --fuzz=5 -p1 -i {patch_filepath}",
            f"    if [ $? -ne 0 ]; then",
            f"        echo '{APPLY_PATCH_FAIL}:'",
            f"        exit 1",
            f"    else",
            f"        echo '{APPLY_PATCH_PASS}:'",
            f"    fi",
            f"else",
            f"    echo '{APPLY_PATCH_PASS}:'",
            f"fi",
        ]

    @property
    def repo_script_list(self) -> list[str]:
        setup_commands = [
            f"git clone -o origin https://github.com/{self.repo} {self.repo_directory}",
            f"chmod -R 777 {self.repo_directory}",
            f"cd {self.repo_directory}",
            f"git reset --hard {self.base_commit}",
            f"git remote remove origin",
            "source /opt/miniconda3/bin/activate",
            f"conda activate {self.env_name}",
            f'echo "Current environment: $CONDA_DEFAULT_ENV"',
        ]
        if self.repo in MAP_REPO_TO_INSTALL:
            setup_commands.append(MAP_REPO_TO_INSTALL[self.repo])

        if "pre_install" in self.specs:
            for pre_install in self.specs["pre_install"]:
                setup_commands.append(pre_install)

        if "install" in self.specs:
            setup_commands.append(self.specs["install"])
        return setup_commands

    @property
    def env_script_list(self) -> list[str]:
        eval_commands = [
            f"source /opt/miniconda3/bin/activate",
            f"conda activate {self.env_name}",
            f"cd {self.repo_directory}",
        ]
        if "eval_commands" in self.specs:
            eval_commands += self.specs["eval_commands"]
        eval_commands += [
            f"git config --global --add safe.directory {self.repo_directory}",
            f"cd {self.repo_directory}",
            f"git status",
            f"git show",
            f"git diff {self.base_commit}",
            "source /opt/miniconda3/bin/activate",
            f"conda activate {self.env_name}",
        ]
        if "install" in self.specs:
            eval_commands.append(self.specs["install"])
        return eval_commands

    def get_test_patch_files(self) -> list:
        diff_pat = r"diff --git a/.* b/(.*)"
        test_patch = self.test_patch
        test_files = re.findall(diff_pat, test_patch)
        return test_files

    def test_script(self, test_files: Optional[list[str]] = None) -> list[str]:
        if not test_files:
            test_files = self.get_test_patch_files()

        directives = [
            d for d in test_files if not any(d.endswith(ext) for ext in NON_TEST_EXTS)
        ]

        if self.repo == "django/django":
            directives_transformed = []
            for d in directives:
                d = d[: -len(".py")] if d.endswith(".py") else d
                d = d[len("tests/") :] if d.startswith("tests/") else d
                d = d.replace("/", ".")
                directives_transformed.append(d)
            directives = directives_transformed

        test_command = " ".join(
            [
                self.specs["test_cmd"],
                *directives,
            ]
        )
        return [f"echo '{RUN_TESTS}'", test_command]

    @property
    def eval_script_list(self) -> list[str]:
        test_files = re.findall(DIFF_MODIFIED_FILE_REGEX, self.test_patch)
        reset_tests_command = f"git checkout {self.base_commit} {' '.join(test_files)}"
        HEREDOC_DELIMITER = "EOF_114329324912"
        apply_test_patch_command = f"git apply -v - <<'{HEREDOC_DELIMITER}'\n{self.test_patch}\n{HEREDOC_DELIMITER}"
        directives = self.get_test_patch_files()
        test_script = self.test_script(directives)

        eval_commands = (
            [reset_tests_command, apply_test_patch_command]
            + test_script
            + [reset_tests_command]
        )

        return eval_commands

    def get_pred_report(self, content: str) -> TestsStatus:
        """
        Generate a report of model evaluation results from a prediction, task instance,
        and evaluation log.

        Args:
            test_spec (TestSpec): test spec containing keys "instance_id", "FAIL_TO_PASS", and "PASS
            instance_id (str): instance ID
            log_path (str): path to evaluation log
            include_tests_status (bool): whether to include the status of each test in the returned report
        Returns:
            report (EvaluationResult): report of metrics
        """

        test_result = parse_log(content, self.repo)
        eval_ref = {
            KEY_INSTANCE_ID: self.instance_id,
            FAIL_TO_PASS: self.fail_to_pass,
            PASS_TO_PASS: self.pass_to_pass,
        }

        report = get_eval_tests_report(test_result, eval_ref)
        status = get_resolution_status(report)

        return TestsStatus(
            status=status,
            fail_to_pass=EvalTestResult(**report[FAIL_TO_PASS]),
            pass_to_pass=EvalTestResult(**report[PASS_TO_PASS]),
        )
