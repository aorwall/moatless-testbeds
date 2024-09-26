import os
import sys
import time
from time import sleep
from typing import Tuple, List, Optional, Dict, Any
import uuid

import requests
import json
import logging
import base64

from requests import RequestException, HTTPError

from testbed.schema import (
    EvaluationResult,
    RunCommandsRequest,
    CommandExecutionResponse,
    TestRunResponse,
)

import testbed.schema

from testbed.swebench.constants import ResolvedStatus, APPLY_PATCH_FAIL, RUN_TESTS
from testbed.swebench.log_parsers import parse_log

from testbed.swebench.test_spec import TestSpec
from testbed.swebench.utils import load_swebench_instance

logger = logging.getLogger(__name__)


class TestbedClient:

    def __init__(self, 
                 testbed_id: str,
                 instance_id: str,
                 base_url: str | None = None, 
                 api_key: str | None = None,
                 log_dir: str | None = None,
                 ignored_tests: dict[str, list[str]] = {}):
        assert testbed_id, "Testbed ID is required"
        assert instance_id, "SWE-bench instance is required"

        base_url = base_url or os.getenv("TESTBED_BASE_URL")
        api_key = api_key or os.getenv("TESTBED_API_KEY")
        assert base_url, "TESTBED_BASE_URL environment variable must be set"
        assert api_key, "TESTBED_API_KEY environment variable must be set"
        logger.info(f"Initializing Testbed SDK with base URL {base_url}")

        base_url = base_url.rstrip('/')

        self.base_url = base_url
        self.headers = {"X-API-Key": api_key}

        self.instance = load_swebench_instance(instance_id)
        self.test_spec = TestSpec.from_instance(self.instance)

        self.testbed_id = testbed_id
        self.ignored_tests = ignored_tests

        if log_dir:
            self.log_dir = f"{log_dir}/{testbed_id}" if log_dir else None
            if not os.path.exists(self.log_dir):
                os.makedirs(self.log_dir)
        else:
            self.log_dir = None

    def check_health(self, timeout: int = 30):
        try:
            data = self._request("GET", "health")
            return data.get("status") == "OK"
        except requests.RequestException as e:
            logger.error(f"Error during ping: {str(e)}")
            return False

    def _request(self, method: str, endpoint: str | None = None, max_retries: int = 3, retry_delay: int = 5, **kwargs) -> Dict[str, Any]:
        url = f"{self.base_url}/testbeds/{self.testbed_id}"
        if endpoint:
           url += f"/{endpoint}"

        retries = 0
        timeout = 30

        while retries < max_retries:
            start_time = time.time()  # Capture start time
            try:
                response = requests.request(method, url, headers=self.headers, timeout=timeout, **kwargs)
                response.raise_for_status()
                return response.json()
            except (RequestException, HTTPError) as e:
                latency = time.time() - start_time  # Calculate latency
                if isinstance(e, HTTPError) and e.response.status_code < 500:
                    logger.error(f"Error during request to {url}: {e}. Response: {e.response.text}. Latency: {latency:.2f} seconds")
                    raise
                retries += 1
                if retries == max_retries:
                    logger.error(f"Max retries reached for {url}: {e}. Latency: {latency:.2f} seconds")
                    raise
                logger.warning(f"Attempt {retries}/{max_retries} to {url} failed: {e}. Retrying in {retry_delay} seconds... Latency: {latency:.2f} seconds")
                time.sleep(retry_delay)
                retry_delay *= 2  # Exponential backoff

    def _execute_command(self, commands: list[str] | str, timeout: int = 60):
        try:
            if isinstance(commands, str):
                commands = commands.split("\n")

            request = RunCommandsRequest(commands=commands, timeout=timeout)
            response = self._request("POST", f"exec", json=request.model_dump())
            cmd_response = CommandExecutionResponse.model_validate(response)

            return cmd_response
        except requests.RequestException as e:
            logger.error(f"Error during execute_commands: {str(e)}")
            raise e

    def wait_until_ready(self, timeout: float = 600):
        start_time = time.time()
        while time.time() - start_time < timeout:
            response = self._request("GET", "status")
            if response.get("status") == "Running":
                return True
            time.sleep(1)
        raise TimeoutError(f"Testbed {self.testbed_id} not ready within {timeout} seconds")

    def status(self):
        return self._request("GET", "status")

    def execute(
        self, commands: list[str] | str, timeout: int = 60
    ) -> CommandExecutionResponse:
        logger.debug(f"Executing commands: {commands}")
        response = self._execute_command(commands, timeout)

        while response.status == "running":
            response = self.get_execution_status()
            sleep(0.1)

        if self.log_dir:
            command_str = "\n".join(commands) if isinstance(commands, list) else commands
            datetime_str = time.strftime("%Y%m%d-%H%M%S")
            with open(f"{self.log_dir}/{datetime_str}_execute.log", "a") as f:
                f.write("\"Commands:\n" + command_str + "\n")
                f.write("\nResponse:\n" + json.dumps(response.model_dump(exclude={"output"})) + "\n")
                f.write("\nOutput:\n" + response.output + "\n")

        return response

    def get_execution_status(self) -> CommandExecutionResponse:
        try:
            response = self._request("GET", "exec")
            return CommandExecutionResponse.model_validate(response)
        except requests.RequestException as e:
            logger.error(f"Error during get_execution_status: {str(e)}")
            raise e

    def reset(self):
        self.execute(self.test_spec.reset_commands)

        response = self.execute("git diff")
        logger.debug(f"Diff after patch: \n{response.output}")
        return response.output

    def apply_patch(self, patch: str) -> str:
        patch_filepath = f"/shared/patch.diff"
        if not patch.endswith('\n'):
            patch += '\n'
        self.save_file(patch_filepath, patch)
        response = self.execute(self.test_spec.patch_commands(patch_filepath))

        if APPLY_PATCH_FAIL in response.output:
            logger.error(f"Failed to apply patch: {patch}.\n\nOutput\n:{response.output}")
            raise RuntimeError(f"Failed to apply patch: {patch}.\n\nOutput\n:{response.output}")

        response = self.execute("git diff")
        logger.debug(f"Diff after patch: \n{response.output}")
        # TODO: Verify that there is a diff?
        return response.output

    def run_tests(
            self,
            test_files: list[str] | None = None,
            patch: str | None = None
    ) -> TestRunResponse:
        logger.info(f"run_tests: test_files={test_files}")

        self.wait_until_ready()
        output = "\n# Reset\n" + self.reset()

        if patch:
            output += "\n# Apply patch\n" + self.apply_patch(patch)

        # TODO: Run self.test_spec.env_script_list after patching?
        commands = []
        commands.extend(self.test_spec.test_script(test_files))
        response = self.execute(commands)

        log = response.output.split(f"{RUN_TESTS}\n")[-1]
        test_result = parse_log(log, self.test_spec.repo)

        output += response.output

        filtered_test_result = []

        statuses = {}

        ignored_tests = 0
        for test in test_result:
            if test.method in self.ignored_tests.get(test.file_path, []):
                ignored_tests += 1
                continue

            filtered_test_result.append(test)

            if test.status not in statuses:
                statuses[test.status] = 0

            statuses[test.status] += 1

        if ignored_tests:
            logger.info(f"Did run {len(test_result)} tests, ignore {ignored_tests} tests. {statuses}")
        else:
            logger.info(f"Did run {len(test_result)} tests. {statuses}")

        return TestRunResponse(
            test_results=filtered_test_result,
            output=output
        )

    def run_evaluation(self, run_id: str | None = None, patch: str | None = None) -> EvaluationResult:
        if not self.instance:
            raise ValueError("SWE-bench instance not set")

        if not patch:
            logger.info(
                f"Running evaluation for instance {self.instance.instance_id} with gold prediction"
            )
            patch = self.instance.patch
        else:
            logger.info(f"Running evaluation for instance {self.instance.instance_id} with patch")

        self.wait_until_ready()
        self.reset()

        run_id = run_id or str(uuid.uuid4())

        patch_filepath = f"/shared/{run_id}/patch.diff"
        self.save_file(patch_filepath, patch)
        response = self.execute(self.test_spec.patch_commands(patch_filepath))

        if "APPLY_PATCH_FAIL" in response.output:
            logger.error("Failed to apply patch")
            return EvaluationResult(
                status="error",
                output=response.output,
            )

        try:
            git_diff_output_before = self.execute(["git diff"]).output.strip()
        except Exception as e:
            logger.warning(f"Failed to get git diff before running eval script: {e}")
            git_diff_output_before = None

        response = self.execute(self.test_spec.eval_script_list)

        while response.status == "running":
            response = self.get_execution_status()
            sleep(1)

        try:
            git_diff_output_after = self.execute("git diff").output.strip()

            if (
                git_diff_output_before
                and git_diff_output_after != git_diff_output_before
            ):
                logger.info(f"Git diff changed after running eval script")
        except Exception as e:
            logger.warning(f"Failed to get git diff after running eval script: {e}")

        test_status = self.test_spec.get_pred_report(response.output)
        return EvaluationResult(
            run_id=run_id,
            resolved=test_status.status == ResolvedStatus.FULL,
            patch_applied=True,
            instance_id=self.instance.instance_id,
            output=response.output,
            tests_status=test_status,
        )

    def save_file(self, file_path: str, content: str):
        try:
            encoded_content = base64.b64encode(content.encode()).decode()
            data = {"file_path": file_path, "content": encoded_content}
            logger.debug(f"Saving file: {file_path}")
            response = self._request("POST", "file", json=data)
            return response
        except requests.RequestException as e:
            logger.error(f"Error saving file {file_path}: {str(e)}")
            raise e
        finally:
            if self.log_dir:
                datetime_str = time.strftime("%Y%m%d-%H%M%S")
                with open(f"{self.log_dir}/{datetime_str}_save_file.log", "a") as f:
                    f.write(f"File path: {file_path}\n")
                    f.write(f"Content:\n{content}\n")

    def get_file(self, file_path: str):
        try:
            params = {"file_path": file_path}
            response = self._request("GET", "file", params=params)
            if "content" in response:
                return base64.b64decode(response["content"]).decode()
            else:
                return response
        except requests.RequestException as e:
            logger.error(f"Error getting file: {str(e)}")
            return {"error": str(e)}

    def destroy(self):
        self._request("DELETE")
