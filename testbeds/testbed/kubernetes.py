import base64
import logging
import os
import time
from collections import namedtuple
from datetime import datetime

from kubernetes import client, config
from kubernetes import config as k8s_config
from kubernetes.client import ApiException
from kubernetes.stream import stream
from opentelemetry import trace

from testbeds.schema import (
    CommandExecutionResponse,
)
from testbeds.testbed.container import Container

logger = logging.getLogger(__name__)

ExecResult = namedtuple("ExecResult", "exit_code,output")

tracer = trace.get_tracer(__name__)


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
        return os.path.exists(os.path.join(self.shared_dir, f"started"))

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
        logger.info(f"Executing commands: {commands}")

        output_file = os.path.join(self.shared_dir, f"cmd_output.txt")
        complete_file = os.path.join(self.shared_dir, f"complete_cmd")
        commands_file = os.path.join(self.shared_dir, f"run_cmd.sh")

        with tracer.start_as_current_span("execute_command") as span:
            if os.path.exists(output_file) and not os.path.exists(complete_file):
                logger.warning("Previous command execution is still running")
                return CommandExecutionResponse(
                    status="running",
                    output=self.read_from_shared_volume("cmd_output.txt"),
                )

            if os.path.exists(output_file):
                os.remove(output_file)

            if os.path.exists(complete_file):
                os.remove(complete_file)

            script_content = "#!/bin/bash\n" + "\n".join(commands)
            self.write_file(commands_file, script_content.encode("utf-8"))

            if not os.path.exists(commands_file):
                logger.error(f"Failed to create commands file: {commands_file}")
                raise FileNotFoundError(f"No such file or directory: '{commands_file}'")

            os.chmod(commands_file, 0o755)

            start_time = datetime.now()
            while not os.path.exists(output_file):
                if (datetime.now() - start_time).seconds > 5:
                    raise Exception(
                        f"No output file found on {output_file} after 5 seconds. This indicates that the command was never executed."
                    )

                time.sleep(0.1)

            return CommandExecutionResponse(
                status="running",
            )

    def get_execution_status(self) -> CommandExecutionResponse:
        commands_file = os.path.join(self.shared_dir, f"run_cmd.sh")
        complete_file = os.path.join(self.shared_dir, f"complete_cmd")
        output_file = os.path.join(self.shared_dir, f"cmd_output.txt")

        with tracer.start_as_current_span("get_execution_status") as span:
            if os.path.exists(complete_file):
                status = "completed"
            elif os.path.exists(commands_file):
                status = "running"
            else:
                status = "idle"

            if os.path.exists(output_file):
                return CommandExecutionResponse(
                    status=status,
                    output=self.read_from_shared_volume(f"cmd_output.txt"),
                )
            else:
                return CommandExecutionResponse(
                    status=status,
                    output="",
                )

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
        logger.debug(f"Reading from shared volume: {shared_path}")
        try:
            with open(shared_path, "r") as file:
                data = file.read()
            logger.debug(f"Successfully read from {shared_path}")
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
        with tracer.start_as_current_span("write_file") as span:
            span.set_attribute("file_path", file_path)
            logger.info(f"Writing file: {file_path}")
            try:
                if file_path.startswith(self.shared_dir):
                    # If the file is in the shared directory, write directly to disk
                    os.makedirs(os.path.dirname(file_path), exist_ok=True)
                    with open(file_path, "wb") as file:
                        file.write(content)
                    logger.info(
                        f"Successfully wrote file to shared volume: {file_path}"
                    )
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
                        raise Exception(
                            "Written content does not match original content"
                        )

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
