import io
import logging
import os
import shutil
import tarfile
from collections import namedtuple
from pathlib import Path
import threading
import time

from kubernetes import client, config
from kubernetes.client import ApiException
from kubernetes.stream import stream

from testbed.container.container import Container

logger = logging.getLogger(__name__)

ExecResult = namedtuple("ExecResult", "exit_code,output")


class KubernetesContainer(Container):

    def __init__(
        self, pod_name: str = os.getenv("POD_NAME"), namespace: str = os.getenv("KUBE_NAMESPACE", "testbeds")
    ):
        self.namespace = namespace
        self.container_name = "testbed"
        self.pod_name = pod_name

        self.core_v1 = client.CoreV1Api()
        self.batch_v1 = client.BatchV1Api()

    def __str__(self):
        return f"Container {self.container_name}:{self.pod_name}:{self.namespace}"

    def is_reachable(self, timeout: int = 10) -> bool:
        """
        Verify that the container is reachable.

        Args:
            timeout (int): Maximum time to wait for a response, in seconds.

        Returns:
            bool: True if the container is reachable, False otherwise.
        """
        try:
            result = self.exec_run("echo 'Container is reachable'", timeout=timeout, retries=10, delay=2)
            logger.info(f"Container reachability check result: {result}")
            return result.exit_code == 0 and "Container is reachable" in result.output
        except Exception as e:
            logger.warning(f"Error checking container reachability: {e}")
            return False

    def exec_run(self, cmd: str, timeout: int | None = None, retries: int = 3, delay: int = 2) -> ExecResult:
        """
        Execute a command in the container with retries.

        Args:
            cmd (str): Command to execute.
            timeout (int | None): Maximum time to wait for a response, in seconds.
            retries (int): Number of retry attempts.
            delay (int): Delay between retries, in seconds.

        Returns:
            ExecResult: Result of the command execution.
        """
        logger.info(f"Executing command: {cmd}")
        exec_command = cmd.split()
        attempt = 0

        while attempt < retries:
            try:
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
                    _preload_content=False
                )
                stdout, stderr = "", ""
                while resp.is_open():
                    resp.update(timeout=1)
                    if resp.peek_stdout():
                        stdout += resp.read_stdout()
                    if resp.peek_stderr():
                        stderr += resp.read_stderr()
                exit_code = resp.returncode

                if stdout and stderr:
                    output = f"STDOUT: {stdout}\nSTDERR: {stderr}"
                elif stdout:
                    output = stdout
                elif stderr:
                    output = stderr
                else:
                    output = ""

                logger.info(f"Command executed with exit code {exit_code}")

                return ExecResult(exit_code=exit_code, output=output)

            except ApiException as e:
                logger.warning(f"Attempt {attempt + 1} failed: {e}")
                attempt += 1
                if attempt < retries:
                    logger.info(f"Retrying in {delay} seconds...")
                    time.sleep(delay)
                else:
                    logger.exception("Failed to execute command")

        raise Exception(f"Failed to execute command: {cmd}")

    def run_eval(self, test_output_path: Path, timeout: int = 1800):
        if os.path.exists(test_output_path):
            os.remove(test_output_path)

        # Trigger the eval script
        with open('/shared/run_eval', 'w') as f:
            pass  # Just create an empty file

        logger.info("Triggered /shared/eval.sh execution")

        start_time = time.time()

        while not os.path.exists('/shared/eval_complete'):
            if time.time() - start_time > timeout:
                with open(test_output_path, "a") as f:
                    f.write(f"\n\nTimeout error: {timeout} seconds exceeded.")

                logger.warning(f"Evaluation timed out after {time.time() - start_time} seconds")
                raise TimeoutError("Evaluation timed out")
            time.sleep(1)

        logger.info(f"Evaluation completed after {time.time() - start_time} seconds. Move test output to {test_output_path}")
        shutil.move('/shared/test_output.txt', test_output_path)

        os.remove('/shared/eval_complete')

    def kill(self):
        with open('/shared/kill', 'w') as f:
            pass
        logger.info("Kill command sent")

    def copy_to_shared_drive(self, src: Path, dst: Path):
        """
        Copy a file from local to a docker container using a shared volume

        Args:
            src (Path): Source file path
            dst (Path): Destination file path in the container
        """

        shared_volume_path = Path("/shared")  # Path where the shared volume is mounted in the container

        # Check if destination path is valid
        if os.path.dirname(dst) == "":
            raise ValueError(
                f"Destination path parent directory cannot be empty!, dst: {dst}"
            )

        # Copy the file to the shared volume
        shared_src_path = shared_volume_path / src.name
        with open(src, "rb") as src_file, open(shared_src_path, "wb") as dst_file:
            dst_file.write(src_file.read())
        logger.debug(f"File copied to shared volume at {shared_src_path}")

        # Make directory in the container if necessary
        self.exec_run(f"mkdir -p {dst.parent}")
        logger.debug(f"Directory created at {dst.parent}")

        # Move the file from the shared volume to the destination in the container
        self.exec_run(f"mv {shared_src_path} {dst}")
        logger.debug(f"File moved to container at {dst}")

    def put_archive(self, path: str, data: bytes | io.BytesIO | str):
        """
        Insert a file or folder in this container using a tar archive as
        source.

        Args:
            path (str): Path inside the container where the file(s) will be
                extracted. Must exist.
            data (bytes or stream): tar data to be extracted

        Returns:
            (bool): True if the call succeeds.
        """
        try:
            logger.info(f"Putting archive {path} with data length {len(data)}")
            if isinstance(data, str):
                data = data.encode("utf-8")

            # Create a tar archive in memory
            tar_stream = io.BytesIO()
            with tarfile.open(fileobj=tar_stream, mode="w") as tar:
                tarinfo = tarfile.TarInfo(name=os.path.basename(path))
                tarinfo.size = len(data)
                tar.addfile(tarinfo, io.BytesIO(data))
            tar_stream.seek(0)

            exec_command = ["tar", "xvf", "-", "-C", os.path.dirname(path)]

            resp = stream(
                self.core_v1.connect_get_namespaced_pod_exec,
                self.pod_name,
                self.namespace,
                command=exec_command,
                container=self.container_name,
                stderr=True,
                stdin=True,
                stdout=True,
                tty=False,
                _preload_content=False,
            )

            # Stream the tar archive to the pod
            while True:
                chunk = tar_stream.read(1024)
                if not chunk:
                    break
                resp.write_stdin(chunk)
            resp.close()

            return True
        except Exception as e:
            logger.warning(f"Error: {e}")
            return False

    def write_to_shared_volume(self, filename: str, data: bytes | str):
        """
        Write data to a file in the shared volume.

        Args:
            filename (str): The name of the file in the shared volume.
            data (bytes or str): The data to write to the file.
        """
        shared_path = f"/shared/{filename}"
        logger.info(f"Writing to shared volume: {shared_path}")
        try:
            if isinstance(data, str):
                data = data.encode("utf-8")
            with open(shared_path, 'wb') as file:
                file.write(data)
            logger.info(f"Successfully wrote to {shared_path}")
        except Exception as e:
            logger.exception(f"Error writing to disk")
