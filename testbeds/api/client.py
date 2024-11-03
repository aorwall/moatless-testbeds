import base64
import json
import logging
import os
import re
import time
from time import sleep
from typing import Optional

import requests
from kubernetes import client

from testbeds.schema import (
    RunCommandsRequest,
    CommandExecutionResponse,
    CommandExecutionSummary,
    TestbedDetailed, TestbedStatusDetailed, ContainerStatus, )
from testbeds.swebench.constants import APPLY_PATCH_FAIL
from testbeds.swebench.test_spec import TestSpec
from testbeds.swebench.utils import load_swebench_instance

logger = logging.getLogger(__name__)


class TestbedClient:

    def __init__(
        self,
        testbed_id: str,
        instance_id: str,
        base_url: str,
        namespace: str = "testbed-dev",
        testbed_namespace: str = "testbed-dev",
        test_spec: TestSpec | None = None,
        startup_timeout=600,
        ignored_tests: dict[str, list[str]] = {},
        in_cluster: bool = False,
    ):
        assert testbed_id, "Testbed ID is required"

        self.testbed_id = testbed_id
        self.namespace = namespace
        self.testbed_namespace = testbed_namespace

        if not base_url:
            self.core_v1 = client.CoreV1Api()
            self.batch_v1 = client.BatchV1Api()
        else:
            self.core_v1 = None
            self.batch_v1 = None

        self._base_url = base_url

        self.ignored_tests = ignored_tests

        self.instance_id = instance_id
        self.test_spec = test_spec
        self.startup_timeout = startup_timeout

        self.in_cluster = in_cluster

    def _get_test_spec(self):
        if not self.test_spec:
            instance = load_swebench_instance(self.instance_id)
            self.test_spec = TestSpec.from_instance(instance)

        return self.test_spec

    @property
    def base_url(self) -> str | None:
        return self._base_url

    def check_health(self, timeout: int = 30) -> dict:
        try:
            response = requests.get(f"{self.base_url}/health", timeout=timeout)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.ConnectionError:
            logger.debug(f"Connection refused to testbed {self.testbed_id} - service likely not ready yet")
            return {"status": "unavailable"}
        except requests.exceptions.RequestException as e:
            logger.error(f"Error checking health for testbed {self.testbed_id}: {str(e)}")
            raise

    def get_testbed(self) -> Optional[TestbedDetailed]:
        job = self._get_job()
        if job:
            status = self._read_testbed_status_detailed(job.metadata.name)
            if status:
                external_ip = None
                if not self.in_cluster:
                    try:
                        external_ip = self._get_service_external_ip()
                    except ValueError:
                        logger.debug(
                            f"External IP not yet available for testbed {self.testbed_id}"
                        )

                return TestbedDetailed(
                    testbed_id=job.metadata.name,
                    instance_id=job.metadata.labels.get("instance-id", "unknown"),
                    status=status,
                    external_ip=external_ip,
                )

        return None

    def _read_testbed_status_detailed(
        self, job_name: str
    ) -> Optional[TestbedStatusDetailed]:
        pod_list = self.core_v1.list_namespaced_pod(
            namespace=self.testbed_namespace, label_selector=f"job-name={job_name}"
        )
        if pod_list.items:
            pod = pod_list.items[0]
            testbed_status = ContainerStatus(
                ready=False, started=False, restart_count=0, state="unknown"
            )
            sidecar_status = ContainerStatus(
                ready=False, started=False, restart_count=0, state="unknown"
            )

            if pod.status and pod.status.container_statuses:
                for container in pod.status.container_statuses:
                    status = self._get_container_status(container)
                    if container.name == "testbed":
                        testbed_status = status
                    elif container.name == "sidecar":
                        sidecar_status = status

            return TestbedStatusDetailed(
                pod_phase=pod.status.phase if pod.status else "Unknown",
                testbed=testbed_status,
                sidecar=sidecar_status,
            )
        else:
            return None

    def _get_service_external_ip(self) -> str:
        service = self.core_v1.read_namespaced_service(
            name=self.testbed_id, namespace=self.testbed_namespace
        )
        if service.status.load_balancer.ingress:
            return service.status.load_balancer.ingress[0].ip
        raise ValueError(f"No external IP found for testbed {self.testbed_id}")

    def _get_container_status(self, container) -> ContainerStatus:
        state = "pending"
        reason = None
        message = None

        if container.state.running:
            state = "running"
        elif container.state.waiting:
            state = "waiting"
            reason = container.state.waiting.reason
            message = container.state.waiting.message
        elif container.state.terminated:
            state = "terminated"
            reason = container.state.terminated.reason
            message = container.state.terminated.message

        return ContainerStatus(
            ready=container.ready,
            started=container.started,
            restart_count=container.restart_count,
            state=state,
            reason=reason,
            message=message,
        )
    
    def _get_job(self):
        try:
            return self.batch_v1.read_namespaced_job(
                name=self.testbed_id, namespace=self.testbed_namespace
            )
        except client.exceptions.ApiException as e:
            if e.status == 404:
                logger.info(f"Job {self.testbed_id} not found in namespace {self.testbed_namespace}.")
                return None
            else:
                raise

    def _execute_command(self, commands: list[str] | str, timeout: int = 60):
        try:
            if isinstance(commands, str):
                commands = commands.split("\n")

            request = RunCommandsRequest(commands=commands, timeout=timeout)
            response = requests.post(f"{self.base_url}/exec", json=request.model_dump())
            response.raise_for_status()

            cmd_response = CommandExecutionResponse.model_validate(response.json())

            return cmd_response
        except requests.RequestException as e:
            logger.error(f"Error during execute_commands: {str(e)}")
            raise e

    def execute(
        self, commands: list[str] | str, timeout: int = 60
    ) -> CommandExecutionResponse:
        logger.debug(f"Executing commands: {commands}")
        response = self._execute_command(commands, timeout)

        while response.status == "running":
            response = self.get_execution_status()
            sleep(0.1)

        return response

    def execute_async(self, commands: list[str] | str) -> CommandExecutionResponse:
        return self._execute_command(commands)

    def get_execution_status(self) -> CommandExecutionResponse:
        try:
            response = requests.get(f"{self.base_url}/exec")
            response.raise_for_status()
            response = CommandExecutionResponse.model_validate(response.json())
            if response.status == "completed":
                logger.info(f"Command execution completed in testbed {self.testbed_id}")
            return response
        except requests.RequestException as e:
            logger.error(f"Error during get_execution_status: {str(e)}")
            raise e

    def get_diff(self) -> str:
        """Get the current git diff output."""
        try:
            response = self.execute("git diff")
            return response.output.strip()
        except requests.RequestException as e:
            logger.error(f"Error getting git diff: {str(e)}")
            raise e

    def reset(self):
        test_spec = self._get_test_spec()
        self.execute(test_spec.reset_commands)
        diff = self.get_diff()
        logger.debug(f"Diff after patch: \n{diff}")
        return diff

    def apply_patch(self, patch: str) -> str:
        if not patch:
            logger.warning(f"apply_patch() No patch provided to apply on testbed {self.testbed_id}")

        logger.info(f"Applying patch to testbed {self.testbed_id}")
        test_spec = self._get_test_spec()

        patch_files = self._get_patch_files(patch)
        for patch_file in patch_files:
            file = self.get_file(patch_file)
            if not file:
                self.save_file(patch_file, "")

        patch_filepath = f"/shared/patch.diff"
        if not patch.endswith('\n'):
            patch += '\n'
        self.save_file(patch_filepath, patch)
        response = self.execute(test_spec.patch_commands(patch_filepath))

        if APPLY_PATCH_FAIL in response.output:
            logger.error(f"Failed to apply patch: {patch}.\n\nOutput\n:{response.output}")
            raise Exception(f"Failed to apply patch: {patch}.\n\nOutput\n:{response.output}")

        diff = self.get_diff()
        logger.debug(f"Diff after patch: \n{diff}")
        return diff

    def _get_patch_files(self, patch: str) -> list:
        diff_pat = r"diff --git a/.* b/(.*)"
        patch_files = re.findall(diff_pat, patch)
        return patch_files

    def run_tests(
            self,
            test_files: list[str] | None = None
    ) -> CommandExecutionResponse:
        logger.info(f"run_tests: test_files={test_files}")
        test_spec = self._get_test_spec()
        commands = test_spec.test_script(test_files)
        return self.execute_async(commands)

    def run_evaluation(self) -> CommandExecutionResponse:
        test_spec = self._get_test_spec()
        return self.execute_async(test_spec.eval_script_list)

    def save_file(self, file_path: str, content: str):
        try:
            encoded_content = base64.b64encode(content.encode()).decode()
            data = {"file_path": file_path, "content": encoded_content}
            logger.debug(f"Saving file: {file_path}")
            response = requests.post(f"{self.base_url}/file", json=data, timeout=30)
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            logger.error(f"Error saving file {file_path}: {str(e)}")
            raise e

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
            if e.response.status_code == 404:
                return None

            logger.error(f"Error getting file: {str(e)}")
            return {"error": str(e)}

    def close(self):
        try:
            response = self.batch_v1.delete_namespaced_job(
                name=self.testbed_id,
                namespace=self.testbed_namespace,
                body=client.V1DeleteOptions(
                    propagation_policy="Foreground", grace_period_seconds=0
                ),
            )

            self.core_v1.delete_namespaced_service(
                name=self.testbed_id,
                namespace=self.testbed_namespace,
                body=client.V1DeleteOptions(
                    propagation_policy="Foreground", grace_period_seconds=0
                ),
            )

            return response

        except client.exceptions.ApiException as e:
            if e.status == 404:
                logger.warning(f"Job {self.testbed_id} not found.")
            else:
                error_message = f"Error deleting job {self.testbed_id}: {str(e)}"
                logger.exception(error_message)
                raise RuntimeError(error_message)
        except Exception as e:
            error_message = (
                f"Unexpected error during cleanup of job {self.testbed_id}: {str(e)}"
            )
            logger.exception(error_message)
            raise RuntimeError(error_message)

        finally:
            self.core_v1.api_client.close()
            self.batch_v1.api_client.close()
