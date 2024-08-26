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
import uuid
import base64
from typing import List

from kubernetes import client, config
from kubernetes.client import ApiException
from kubernetes.stream import stream
from kubernetes import config as k8s_config

from testbed.container.container import Container
from testbed.schema import (
    CommandStatusResponse,
    CommandExecutionResponse,
    CommandExecutionSummary,
)

logger = logging.getLogger(__name__)

ExecResult = namedtuple("ExecResult", "exit_code,output")


class KubernetesContainer(Container):
    def __init__(
        self,
        pod_name: str = os.getenv("POD_NAME"),
        namespace: str = os.getenv("KUBE_NAMESPACE", "testbeds"),
        timeout: int = 1800,
        shared_dir: str = "/shared",
    ):
        try:
            k8s_config.load_incluster_config()
            logger.info("Loaded in-cluster Kubernetes configuration")
        except k8s_config.ConfigException:
            try:
                k8s_config.load_kube_config()
                logger.info("Loaded Kubernetes configuration from default location")
            except k8s_config.ConfigException:
                logger.error("Could not load Kubernetes configuration")

        self.namespace = namespace
        self.container_name = "testbed"
        self.pod_name = pod_name
        self.timeout = timeout
        self.started_at = False
        self.shared_dir = shared_dir

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
                result = self._exec(
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

    def _exec(
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

    def execute(
        self, commands: list[str], timeout: int = 60
    ) -> CommandExecutionResponse:
        execution_id = str(uuid.uuid4())
        commands_file = f"/tmp/{execution_id}_cmd.sh"

        # Create a shell script with the commands
        script_content = "#!/bin/bash\n" + "\n".join(commands)
        self.write_file(commands_file, script_content.encode("utf-8"))

        # Make the script executable
        self._exec(f"chmod +x {commands_file}")

        try:
            # Execute the script with the built-in timeout
            result = stream(
                self.core_v1.connect_get_namespaced_pod_exec,
                self.pod_name,
                self.namespace,
                container=self.container_name,
                command=["/bin/bash", commands_file],
                stderr=True,
                stdin=False,
                stdout=True,
                tty=False,
                _preload_content=False,
                _request_timeout=timeout,
            )

            output = ""
            while result.is_open():
                result.update(timeout=1)
                if result.peek_stdout():
                    output += result.read_stdout()
                if result.peek_stderr():
                    output += result.read_stderr()

            status = "completed"
        except Exception as e:
            logger.error(f"Command execution failed or timed out: {str(e)}")
            output = f"Error: {str(e)}"
            status = "failed"

        # Clean up the temporary script
        self._exec(f"rm {commands_file}")

        return CommandExecutionResponse(
            execution_id=execution_id,
            status=status,
            output=output,
        )

    def get_execution_status(self, execution_id: str) -> CommandExecutionResponse:
        complete_file = os.path.join(self.shared_dir, f"{execution_id}_complete.txt")
        output_file = os.path.join(self.shared_dir, f"{execution_id}_output.txt")

        if os.path.exists(complete_file):
            status = "completed"
        else:
            status = "running"

        return CommandExecutionResponse(
            execution_id=execution_id,
            status=status,
            output=self.read_from_shared_volume(f"{execution_id}_output.txt"),
        )

    def list_executed_commands(self) -> List[CommandExecutionSummary]:
        commands_dir = os.path.join(self.shared_dir, "commands")
        summaries = []

        for filename in os.listdir(commands_dir):
            if filename.endswith("_cmd.txt"):
                execution_id = filename.split("_")[0]
                complete_file = os.path.join(
                    commands_dir, f"{execution_id}_complete.txt"
                )
                output_file = os.path.join(commands_dir, f"{execution_id}_output.txt")

                status = "completed" if os.path.exists(complete_file) else "running"

                with open(os.path.join(commands_dir, filename), "r") as cmd_file:
                    commands = cmd_file.read().splitlines()

                summary = CommandExecutionSummary(
                    execution_id=execution_id,
                    status=status,
                    commands=commands,
                    output_file=output_file,
                )
                summaries.append(summary)

        return summaries

    def is_executing(self):
        if not self.started_at:
            return False

        if os.path.exists(os.path.join(self.shared_dir, "complete_cmd")):
            self.started_at = None
            return False

        return True

    def get_output(self) -> str:
        return self.read_from_shared_volume("cmd_output.txt")

    def read_from_shared_volume(self, filename: str) -> str:
        shared_path = os.path.join(self.shared_dir, filename)
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
        shared_path = os.path.join(self.shared_dir, filename)
        logger.info(f"Writing to shared volume: {shared_path}")
        try:
            os.makedirs(os.path.dirname(shared_path), exist_ok=True)
            if isinstance(data, str):
                data = data.encode("utf-8")
            with open(shared_path, "wb") as file:
                file.write(data)
            logger.info(f"Successfully wrote to {shared_path}")
        except Exception as e:
            logger.exception(f"Error writing to disk")

    def write_file(self, file_path: str, content: bytes):
        logger.info(f"Writing file: {file_path}")
        try:
            if file_path.startswith(self.shared_dir):
                # If the file is in the shared directory, write directly to disk
                os.makedirs(os.path.dirname(file_path), exist_ok=True)
                with open(file_path, "wb") as file:
                    file.write(content)
                logger.info(f"Successfully wrote file to shared volume: {file_path}")
            else:
                # For files outside the shared directory, use the existing method
                encoded_content = base64.b64encode(content).decode("utf-8")
                exec_command = [
                    "sh",
                    "-c",
                    f"mkdir -p $(dirname {file_path}) && echo '{encoded_content}' | base64 -d > {file_path} && cat {file_path} | base64",
                ]
                resp = stream(
                    self.core_v1.connect_get_namespaced_pod_exec,
                    self.pod_name,
                    self.namespace,
                    container=self.container_name,
                    command=exec_command,
                    stderr=True,
                    stdin=False,
                    stdout=True,
                    tty=False,
                )

                # Decode the response and compare with original content
                written_content = base64.b64decode(resp.strip())
                if written_content != content:
                    raise Exception("Written content does not match original content")

                logger.info(
                    f"Successfully wrote and verified file in testbed container: {file_path}"
                )
        except Exception as e:
            logger.exception(f"Error writing file: {file_path}")
            raise

    def read_file(self, file_path: str) -> str:
        logger.info(f"Reading file from testbed container: {file_path}")
        try:
            exec_command = ["cat", file_path]
            resp = stream(
                self.core_v1.connect_get_namespaced_pod_exec,
                self.pod_name,
                self.namespace,
                container=self.container_name,
                command=exec_command,
                stderr=True,
                stdin=False,
                stdout=True,
                tty=False,
            )
            logger.info(f"Successfully read file from testbed container: {file_path}")
            return resp
        except Exception as e:
            logger.exception(f"Error reading file from testbed container: {file_path}")
            raise

    def kill(self):
        logger.info(f"Killing pod {self.pod_name} in namespace {self.namespace}")
        try:
            self.write_to_shared_volume("kill", "")

        except ApiException as e:
            logger.error(f"Error killing pod {self.pod_name}: {e}")
            raise
