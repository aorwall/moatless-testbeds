import os
import sys
import time
from time import sleep
from typing import Tuple, List, Optional
import uuid

import requests
import json
import logging
import base64

from kubernetes import client

from testbed.schema import (
    EvaluationResult,
    RunCommandsRequest,
    CommandExecutionResponse,
    CommandExecutionSummary,
    CommandExecutionSummary,
    SWEbenchInstance,
    TestResult, TestbedDetailed, TestbedStatusDetailed, ContainerStatus, TestRunResponse,
)
from testbed.swebench.constants import ResolvedStatus, APPLY_PATCH_FAIL, RUN_TESTS
from testbed.swebench.log_parsers import parse_log

from testbed.swebench.test_spec import TestSpec

logger = logging.getLogger(__name__)


class TestbedClient:

    def __init__(
        self,
        testbed_id: str,
        instance: SWEbenchInstance,
        port: int = 8000,
        namespace: str = "testbed-dev",
        testbed_namespace: str = "testbed-dev",
        log_dir: str | None = None,
        test_spec: TestSpec | None = None,
        startup_timeout=600,
        ignored_tests: dict[str, list[str]] = {},
        in_cluster: bool = False,
    ):
        assert testbed_id, "Testbed ID is required"
        assert instance, "SWE-bench instance is required"

        self.testbed_id = testbed_id
        self.namespace = namespace
        self.testbed_namespace = testbed_namespace

        self.core_v1 = client.CoreV1Api()
        self.batch_v1 = client.BatchV1Api()

        self.hostname = None
        self.port = port

        self.ignored_tests = ignored_tests

        if log_dir:
            self.log_dir = f"{log_dir}/{testbed_id}" if log_dir else None
            if not os.path.exists(self.log_dir):
                os.makedirs(self.log_dir)
        else:
            self.log_dir = None

        self.instance = instance
        self.startup_timeout = startup_timeout

        if test_spec:
            self.test_spec = test_spec
        else:
            self.test_spec = TestSpec.from_instance(self.instance)

        self.in_cluster = in_cluster

    def wait_until_ready(self, timeout: int | None = None, interval = 1):
        start_time = time.time()
        last_status = {}

        timeout = timeout or self.startup_timeout

        def print_status(status, elapsed_time):
            output = [
                f"Testbed ID: {self.testbed_id}",
                f"Pod Phase: {status['pod_phase']}",
                f"External IP: {status['external_ip'] or 'Not assigned yet'}" if not self.in_cluster else "Not applicable",
                "Testbed Container:",
                f"  Ready: {status['testbed_ready']}",
                f"  State: {status['testbed_state']}",
                "Sidecar Container:",
                f"  Ready: {status['sidecar_ready']}",
                f"  State: {status['sidecar_state']}",
                f"\nWaiting for testbed to be ready... (Elapsed time: {int(elapsed_time)}s)",
            ]
            if status["testbed_reason"]:
                output.insert(5, f"  Reason: {status['testbed_reason']}")
            if status["sidecar_reason"]:
                output.insert(-1, f"  Reason: {status['sidecar_reason']}")

            if "IPython" in sys.modules:
                from IPython.display import clear_output

                clear_output(wait=True)
                print("\n".join(output))
            else:
                logger.info("\n".join(output))

        while time.time() - start_time < timeout:
            testbed = self.get_testbed()
            if not testbed:
                current_status = {
                    "pod_phase": "Not found",
                    "external_ip": "Not found",
                    "testbed_ready": False,
                    "testbed_state": "Not found",
                    "testbed_reason": "Not found",
                    "sidecar_ready": False,
                    "sidecar_state": "Not found",
                    "sidecar_reason": "Not found",
                }
            else:
                current_status = {
                    "pod_phase": testbed.status.pod_phase,
                    "external_ip": testbed.external_ip,
                    "testbed_ready": testbed.status.testbed.ready,
                    "testbed_state": testbed.status.testbed.state,
                    "testbed_reason": testbed.status.testbed.reason,
                    "sidecar_ready": testbed.status.sidecar.ready,
                    "sidecar_state": testbed.status.sidecar.state,
                    "sidecar_reason": testbed.status.sidecar.reason,
                }

            if current_status != last_status:
                print_status(current_status, time.time() - start_time)
                last_status = current_status

            if (
                current_status["pod_phase"] == "Running"
                and (current_status["external_ip"] or self.in_cluster)
                and current_status["testbed_ready"]
                and current_status["sidecar_ready"]
            ):
                base_url = self._create_base_url(current_status["external_ip"], self.port)
                try:
                    response = requests.get(f"{base_url}/health", timeout=10)
                    data = response.json()
                    if data.get("status") == "OK":
                        finish_text = f"Testbed {self.testbed_id} is ready and can be reached on {base_url}!"
                        if "IPython" in sys.modules:
                            from IPython.display import clear_output

                            print(finish_text)
                        else:
                            logger.info(finish_text)
                        return testbed
                except Exception as e:
                    logger.info(
                        f"Testbed {self.testbed_id} is ready but not reachable on {base_url}: {e}"
                    )

            time.sleep(interval)

        if "IPython" in sys.modules:
            print(
                f"\nTimeout reached. Testbed {self.testbed_id} is not fully ready after {timeout} seconds."
            )
        else:
            logger.error(
                f"\nTimeout reached. Testbed {self.testbed_id} is not fully ready after {timeout} seconds."
            )
        return None

    def _create_base_url(self, hostname: str, port: int):
        if self.in_cluster:
            return f"http://{self.testbed_id}.{self.namespace}.svc.cluster.local:{self.port}"

        return f"http://{hostname}:{port}"

    @property
    def base_url(self) -> str | None:
        if self.in_cluster:
            return f"http://{self.testbed_id}.{self.namespace}.svc.cluster.local:{self.port}"
        
        if self.hostname:
            return self._create_base_url(self.hostname, self.port)

        testbed = self.wait_until_ready(timeout=self.startup_timeout)
        if testbed.external_ip:
            self.hostname = testbed.external_ip
            return self.base_url

    def check_health(self, timeout: int = 30):
        try:
            response = requests.get(f"{self.base_url}/health", timeout=timeout)
            response.raise_for_status()
            data = response.json()
            return data.get("status") == "OK"
        except requests.RequestException as e:
            logger.error(f"Error during ping: {str(e)}")
            return False

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

        if self.log_dir:
            command_str = "\n".join(commands) if isinstance(commands, list) else commands
            datetime_str = time.strftime("%Y%m%d-%H%M%S")
            with open(f"{self.log_dir}/{datetime_str}_execute.log", "a") as f:
                f.write("\"Commands:\n" + command_str + "\n")
                f.write("\nResponse:\n" + json.dumps(response.model_dump(exclude={"output"})) + "\n")
                f.write("\nOutput:\n" + response.output + "\n")

        return response

    def execute_async(self, commands: list[str] | str) -> CommandExecutionResponse:
        return self._execute_command(commands)

    def get_execution_status(self) -> CommandExecutionResponse:
        try:
            response = requests.get(f"{self.base_url}/exec")
            response.raise_for_status()
            return CommandExecutionResponse.model_validate(response.json())
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

    def reset(self):
        self.execute(self.test_spec.reset_commands)

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

        if patch:
            self.reset()
            self.apply_patch(patch)

        # TODO: Run self.test_spec.env_script_list after patching?
        commands = []
        commands.extend(self.test_spec.test_script(test_files))
        response = self.execute(commands)

        log = response.output.split(f"{RUN_TESTS}\n")[-1]
        test_result = parse_log(log, self.test_spec.repo)

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
            output=response.output
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
            response = requests.post(f"{self.base_url}/file", json=data, timeout=30)
            response.raise_for_status()
            return response.json()
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
            self.hostname = None
