import hashlib
import json
import platform
import re
from dataclasses import dataclass
from typing import Any, Union

from testbed.schema import SWEbenchInstance
from testbed.swebench.constants import (
    MAP_REPO_TO_INSTALL,
    MAP_REPO_VERSION_TO_SPECS,
    USE_X86,
)
from testbed.swebench.utils import get_test_directives

DIFF_MODIFIED_FILE_REGEX = r"--- a/(.*)"


@dataclass
class TestSpec:
    """
    A dataclass that represents a test specification for a single instance of SWE-bench.
    """

    instance_id: str
    repo: str
    version: str
    repo_script_list: list[str]
    eval_script_list: list[str]
    arch: str
    FAIL_TO_PASS: list[str]
    PASS_TO_PASS: list[str]

    @property
    def eval_script(self):
        return (
            "\n".join(["#!/bin/bash", "set -uxo pipefail"] + self.eval_script_list)
            + "\n"
        )
        # Don't exit early because we need to revert tests at the end

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


def make_repo_script_list(specs, repo, repo_directory, base_commit, env_name):
    """
    Create a list of bash commands to set up the repository for testing.
    This is the setup script for the instance image.
    """
    setup_commands = [
        f"git clone -o origin https://github.com/{repo} {repo_directory}",
        f"chmod -R 777 {repo_directory}",  # So nonroot user can run tests
        f"cd {repo_directory}",
        f"git reset --hard {base_commit}",
        # Remove the remote so the agent won't see newer commits.
        f"git remote remove origin",
        # Make sure conda is available for later use
        "source /opt/miniconda3/bin/activate",
        f"conda activate {env_name}",
        f'echo "Current environment: $CONDA_DEFAULT_ENV"',
    ]
    if repo in MAP_REPO_TO_INSTALL:
        setup_commands.append(MAP_REPO_TO_INSTALL[repo])

    # Run pre-install set up if provided
    if "pre_install" in specs:
        for pre_install in specs["pre_install"]:
            setup_commands.append(pre_install)

    if "install" in specs:
        setup_commands.append(specs["install"])
    return setup_commands

def make_env_setup_script_list(instance: SWEbenchInstance) -> list[str]:
    env_name = "testbed"
    repo_directory = f"/{env_name}"
    specs = MAP_REPO_VERSION_TO_SPECS[instance.repo][instance.version]
    base_commit = instance.base_commit

    eval_commands = [
        f"source /opt/miniconda3/bin/activate",
        f"conda activate {env_name}",
        f"cd {repo_directory}",
    ]
    if "eval_commands" in specs:
        eval_commands += specs["eval_commands"]
    eval_commands += [
        f"git config --global --add safe.directory {repo_directory}",  # for nonroot user
        f"cd {repo_directory}",
        # This is just informational, so we have a record
        f"git status",
        f"git show",
        f"git diff {base_commit}",
        "source /opt/miniconda3/bin/activate",
        f"conda activate {env_name}",
    ]
    if "install" in specs:
        eval_commands.append(specs["install"])
    return eval_commands


def get_test_command(instance: SWEbenchInstance):
    return " ".join(
        [
            MAP_REPO_VERSION_TO_SPECS[instance.repo][instance.version][
                "test_cmd"
            ],
            *get_test_directives(instance),
        ]
    )

def get_evaluation_commands(instance: SWEbenchInstance):
    test_files = re.findall(DIFF_MODIFIED_FILE_REGEX, instance.test_patch)
    reset_tests_command = f"git checkout {instance.base_commit} {' '.join(test_files)}"
    HEREDOC_DELIMITER = "EOF_114329324912"

    apply_test_patch_command = (
        f"git apply -v - <<'{HEREDOC_DELIMITER}'\n{instance.test_patch}\n{HEREDOC_DELIMITER}"
    )

    return [
        f"git checkout {instance.base_commit} {' '.join(test_files)}",
        # apply_test_patch_command,
        get_test_command(instance),
        reset_tests_command,  # Revert tests after done, leave the repo in the same state as before
    ]


def make_eval_script_list(
    instance, specs, env_name, repo_directory, base_commit, test_patch
):
    """
    Applies the test patch and runs the tests.
    """
    HEREDOC_DELIMITER = "EOF_114329324912"
    test_files = re.findall(DIFF_MODIFIED_FILE_REGEX, test_patch)
    # Reset test files to the state they should be in before the patch.
    reset_tests_command = f"git checkout {base_commit} {' '.join(test_files)}"
    apply_test_patch_command = (
        f"git apply -v - <<'{HEREDOC_DELIMITER}'\n{test_patch}\n{HEREDOC_DELIMITER}"
    )

    test_command = " ".join(
        [
            MAP_REPO_VERSION_TO_SPECS[instance["repo"]][instance["version"]][
                "test_cmd"
            ],
            *get_test_directives(instance),
        ]
    )

    eval_commands = [
        f"source /opt/miniconda3/bin/activate",
        f"conda activate {env_name}",
        f"cd {repo_directory}",
    ]
    if "eval_commands" in specs:
        eval_commands += specs["eval_commands"]
    eval_commands += [
        f"git config --global --add safe.directory {repo_directory}",  # for nonroot user
        f"cd {repo_directory}",
        # This is just informational, so we have a record
        f"git status",
        f"git show",
        f"git diff {base_commit}",
        "source /opt/miniconda3/bin/activate",
        f"conda activate {env_name}",
    ]
    if "install" in specs:
        eval_commands.append(specs["install"])
    eval_commands += [
        reset_tests_command,
        apply_test_patch_command,
        test_command,
        reset_tests_command,  # Revert tests after done, leave the repo in the same state as before
    ]
    return eval_commands


def make_test_spec(instance: SWEbenchInstance) -> TestSpec:
    if isinstance(instance, TestSpec):
        return instance
    env_name = "testbed"
    repo_directory = f"/{env_name}"
    specs = MAP_REPO_VERSION_TO_SPECS[instance.repo][instance.version]

    repo_script_list = make_repo_script_list(
        specs, instance.repo, repo_directory, instance.base_commit, env_name
    )
    eval_script_list = make_eval_script_list(
        instance, specs, env_name, repo_directory, instance.base_commit, instance.test_patch
    )
    if platform.machine() in {"aarch64", "arm64"}:
        # use arm64 unless explicitly specified
        arch = "arm64" if instance.instance_id not in USE_X86 else "x86_64"
    else:
        arch = "x86_64"

    return TestSpec(
        instance_id=instance.instance_id,
        repo=instance.repo,
        repo_script_list=repo_script_list,
        eval_script_list=eval_script_list,
        version=instance.version,
        arch=arch,
        FAIL_TO_PASS=instance.fail_to_pass,
        PASS_TO_PASS=instance.pass_to_pass,
    )
