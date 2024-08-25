import io
import logging
import os
import shutil
import tarfile
from collections import namedtuple
from datetime import datetime
from pathlib import Path
import threading
import time

from kubernetes import client, config
from kubernetes.client import ApiException
from kubernetes.stream import stream

from testbed.container.container import Container
from testbed.schema import CommandStatusResponse

logger = logging.getLogger(__name__)

ExecResult = namedtuple("ExecResult", "exit_code,output")


class KubernetesContainer(Container):
    def __init__(
        self,
        pod_name: str = os.getenv("POD_NAME"),
        namespace: str = os.getenv("KUBE_NAMESPACE", "testbeds"),
        timeout: int = 1800,
    ):
        self.namespace = namespace
        self.container_name = "testbed"
        self.pod_name = pod_name
        self.timeout = timeout
        self.started_at = False

        self.last_executed_commands = []

        # Load the kubeconfig
        try:
            config.load_incluster_config()
            logger.info("Loaded in-cluster Kubernetes configuration")
        except config.ConfigException:
            try:
                config.load_kube_config()
                logger.info("Loaded Kubernetes configuration from default location")
            except config.ConfigException:
                logger.error("Could not load Kubernetes configuration")
                raise

        self.core_v1 = client.CoreV1Api()
        self.batch_v1 = client.BatchV1Api()

        logger.info(
            f"Initialized KubernetesContainer with pod_name={self.pod_name}, namespace={self.namespace}"
        )

    def __str__(self):
        return f"Container {self.container_name}:{self.pod_name}:{self.namespace}"

    def is_reachable(self, timeout: int = 10) -> bool:
        start_time = time.time()
        while time.time() - start_time < timeout:
            try:
                result = self.exec_run(
                    "echo 'Container is reachable'", timeout=5, retries=1
                )
                if result.exit_code == 0 and "Container is reachable" in result.output:
                    logger.debug("Container is reachable")
                    return True
                else:
                    logger.warning(f"Unexpected result: {result}")
            except Exception as e:
                logger.warning(f"Error checking container reachability: {e}")
            time.sleep(1)

        logger.error(
            f"Container {self.pod_name} in namespace {self.namespace} is not reachable after {timeout} seconds"
        )
        return False

    def is_pod_ready(self) -> bool:
        try:
            pod = self.core_v1.read_namespaced_pod(
                name=self.pod_name, namespace=self.namespace
            )
            for condition in pod.status.conditions:
                if condition.type == "Ready" and condition.status == "True":
                    logger.info(f"Pod {self.pod_name} is ready")
                    return True
            logger.warning(f"Pod {self.pod_name} is not ready")
            return False
        except ApiException as e:
            logger.error(f"Error checking pod readiness: {e}")
            return False

    def exec_run(
        self, cmd: str, timeout: int | None = None, retries: int = 3, delay: int = 2
    ) -> ExecResult:
        logger.debug(
            f"Executing command in pod {self.pod_name}, namespace {self.namespace}: {cmd}"
        )
        exec_command = cmd.split()
        attempt = 0

        while attempt < retries:
            try:
                logger.debug(
                    f"Attempt {attempt + 1}: Calling connect_get_namespaced_pod_exec"
                )
                resp = stream(
                    self.core_v1.connect_get_namespaced_pod_exec,
                    self.pod_name,
                    self.namespace,
                    container=self.container_name,
                    command=exec_command,
                    _request_timeout=timeout,
                    stderr=True,
                    stdin=False,
                    stdout=True,
                    tty=False,
                    _preload_content=False,
                )
                logger.debug("Stream object created successfully")

                stdout, stderr = "", ""
                try:
                    while resp.is_open():
                        resp.update(timeout=1)
                        if resp.peek_stdout():
                            stdout += resp.read_stdout()
                        if resp.peek_stderr():
                            stderr += resp.read_stderr()
                except Exception as e:
                    logger.error(f"Error while reading from stream: {e}")
                    raise

                exit_code = resp.returncode
                logger.debug(f"Command execution completed with exit code: {exit_code}")

                if stdout and stderr:
                    output = f"STDOUT: {stdout}\nSTDERR: {stderr}"
                elif stdout:
                    output = stdout
                elif stderr:
                    output = stderr
                else:
                    output = ""

                logger.debug(f"Command executed with exit code {exit_code}")
                return ExecResult(exit_code=exit_code, output=output)

            except ApiException as e:
                logger.warning(
                    f"Attempt {attempt + 1}/{retries} to execute command `{cmd}` on {self} failed: {e}"
                )
                logger.debug(f"API Exception details: {e.body}")
                attempt += 1
                if attempt < retries:
                    logger.info(f"Retrying in {delay} seconds...")
                    time.sleep(delay)
                else:
                    logger.error(
                        f"Failed to execute command after {retries} attempts: {cmd}"
                    )
                    raise

        raise Exception(f"Failed to execute command: {cmd}")

    def exec_commands(self, commands: list[str]):
        if self.is_executing():
            raise Exception("Container is already running")

        command_str = "#!/bin/bash\n" + "\n".join(commands)
        self.write_to_shared_volume("commands.sh", command_str)
        os.chmod("/shared/commands.sh", 0o755)  # rwxr-xr-x permissions

        with open("/shared/cmd_output.txt", "w") as f:
            f.write("")

        if os.path.exists("/shared/complete_cmd"):
            os.remove("/shared/complete_cmd")

        # Trigger commands.sh script
        with open("/shared/run_cmd", "w") as f:
            pass  # Just create an empty file

        self.started_at = datetime.now()
        self.last_executed_commands = commands

        logger.info("Triggered /shared/commands.sh execution")

    def is_executing(self):
        if not self.started_at:
            return False

        if os.path.exists("/shared/complete_cmd"):
            self.started_at = None
            return False

        return True

    def get_output(self) -> str:
        return self.read_from_shared_volume("cmd_output.txt")

    def run_eval(self, test_output_path: Path, timeout: int = 1800):
        if os.path.exists(test_output_path):
            os.remove(test_output_path)

        # Trigger the eval script
        with open("/shared/run_eval", "w") as f:
            pass  # Just create an empty file

        logger.info("Triggered /shared/eval.sh execution")

        start_time = time.time()

        while not os.path.exists("/shared/eval_complete"):
            if time.time() - start_time > timeout:
                with open(test_output_path, "a") as f:
                    f.write(f"\n\nTimeout error: {timeout} seconds exceeded.")

                logger.warning(
                    f"Evaluation timed out after {time.time() - start_time} seconds"
                )
                raise TimeoutError("Evaluation timed out")
            time.sleep(1)

        logger.info(
            f"Evaluation completed after {time.time() - start_time} seconds. Move test output to {test_output_path}"
        )
        shutil.move("/shared/test_output.txt", test_output_path)

        os.remove("/shared/eval_complete")

    def kill(self):
        with open("/shared/kill", "w") as f:
            pass
        logger.info("Kill command sent")

    def read_from_shared_volume(self, filename: str) -> str:
        shared_path = f"/shared/{filename}"
        logger.info(f"Reading from shared volume: {shared_path}")
        try:
            with open(shared_path, "r") as file:
                data = file.read()
            logger.info(f"Successfully read from {shared_path}")
            return data
        except Exception as e:
            logger.exception(f"Error reading from disk")
            return ""

    def write_to_shared_volume(self, filename: str, data: bytes | str):
        shared_path = f"/shared/{filename}"
        logger.info(f"Writing to shared volume: {shared_path}")
        try:
            if isinstance(data, str):
                data = data.encode("utf-8")
            with open(shared_path, "wb") as file:
                file.write(data)
            logger.info(f"Successfully wrote to {shared_path}")
        except Exception as e:
            logger.exception(f"Error writing to disk")