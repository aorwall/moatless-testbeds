from time import sleep

import requests
import json
import logging
import base64

from testbed.schema import (
    EvaluationResult,
    Prediction,
    RunEvaluationRequest,
    CommandStatusResponse,
    RunCommandsRequest,
    CommandExecutionResponse,
    CommandExecutionSummary,
    SWEbenchInstance,
    TestsStatus,
    TestResult,
)

from testbed.swebench.test_spec import TestSpec

logger = logging.getLogger(__name__)


class TestbedClient:
    def __init__(
        self,
        testbed_id: str,
        host: str = "localhost",
        port: int = 8000,
        instance: SWEbenchInstance | None = None,
    ):
        self.testbed_id = testbed_id
        self.base_url = f"http://{host}:{port}"
        self.instance = instance
        if instance:
            self.test_spec = TestSpec.from_instance(instance)

    def check_health(self, timeout: int = 30):
        try:
            response = requests.get(f"{self.base_url}/health", timeout=timeout)
            response.raise_for_status()
            data = response.json()
            return data.get("status") == "OK"
        except requests.RequestException as e:
            logger.error(f"Error during ping: {str(e)}")
            return False

    def _execute_command(self, commands: list[str] | str, timeout: int = 60):
        try:
            if isinstance(commands, str):
                commands = commands.split("\n")

            request = RunCommandsRequest(commands=commands, timeout=timeout)
            response = requests.post(f"{self.base_url}/exec", json=request.model_dump())
            response.raise_for_status()
            return CommandExecutionResponse.model_validate(response.json())
        except requests.RequestException as e:
            logger.error(f"Error during execute_commands: {str(e)}")
            raise e

    def execute(
        self, commands: list[str] | str, timeout: int = 60
    ) -> CommandExecutionResponse:
        response = self._execute_command(commands, timeout)

        while response.status == "running":
            cmd_response = self.get_execution_status(response.execution_id)
            print(cmd_response.output)
            sleep(1)

        return response

    def execute_async(self, commands: list[str] | str) -> CommandExecutionResponse:
        return self._execute_command(commands)

    def get_execution_status(self, execution_id: str) -> CommandStatusResponse:
        try:
            response = requests.get(f"{self.base_url}/exec/{execution_id}")
            response.raise_for_status()
            return CommandStatusResponse.model_validate(response.json())
        except requests.RequestException as e:
            logger.error(f"Error during get_execution_status: {str(e)}")
            raise e

    def list_executed_commands(self) -> list[CommandExecutionSummary]:
        try:
            response = requests.get(f"{self.base_url}/exec")
            response.raise_for_status()
            return [
                CommandExecutionSummary.model_validate(item) for item in response.json()
            ]
        except requests.RequestException as e:
            logger.error(f"Error during list_executed_commands: {str(e)}")
            raise e

    def run_evaluation(self, run_id: str, patch: str | None = None) -> EvaluationResult:
        if not self.instance:
            raise ValueError("SWE-bench instance not set")

        if not patch:
            logger.info(
                f"Running evaluation for instance {self.instance.instance_id} with gold prediction"
            )
            patch = self.instance.patch

        patch_filepath = f"/shared/{run_id}/patch.diff"
        self.save_file(patch_filepath, patch)
        response = self.execute(self.test_spec.patch_commands(patch_filepath))

        if "APPLY_PATCH_FAIL" in response.output:
            logger.error("Failed to apply patch")
            return EvaluationResult(
                status="error",
                message="Failed to apply patch",
                output=response.output,
            )

        try:
            git_diff_output_before = self.execute(["git diff"]).output.strip()
        except Exception as e:
            logger.warning(f"Failed to get git diff before running eval script: {e}")
            git_diff_output_before = None

        response = self.execute(self.test_spec.eval_script_list)

        while response.status == "running":
            response = self.get_execution_status(response.execution_id)
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
            status="completed",
            instance_id=self.instance.instance_id,
            message="Evaluation completed",
            output=response.output,
            tests_status=test_status,
        )

    def save_file(self, file_path: str, content: str):
        try:
            encoded_content = base64.b64encode(content.encode()).decode()
            data = {"file_path": file_path, "content": encoded_content}
            response = requests.post(f"{self.base_url}/file", json=data)
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            logger.error(f"Error saving file: {str(e)}")
            return {"error": str(e)}

    def get_file(self, file_path: str):
        try:
            params = {"file_path": file_path}
            response = requests.get(f"{self.base_url}/file", params=params)
            response.raise_for_status()
            data = response.json()
            if "content" in data:
                return base64.b64decode(data["content"]).decode()
            else:
                return data
        except requests.RequestException as e:
            logger.error(f"Error getting file: {str(e)}")
            return {"error": str(e)}
