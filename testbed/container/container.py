import logging
from abc import ABC, abstractmethod
from collections import namedtuple
from pathlib import Path

logger = logging.getLogger(__name__)

ExecResult = namedtuple("ExecResult", "exit_code,output")


class Container(ABC):
    @abstractmethod
    def is_reachable(self, timeout: int = 10) -> bool:
        """
        Verify that the container is reachable.

        Args:
            timeout (int): Maximum time to wait for a response, in seconds.

        Returns:
            bool: True if the container is reachable, False otherwise.
        """
        pass

    @abstractmethod
    def exec_run(
        self, cmd: str, timeout: int | None = None, retries: int = 3, delay: int = 2
    ) -> ExecResult:
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
        pass

    @abstractmethod
    def run_eval(self, test_output_path: Path, timeout: int = 1800):
        """
        Run the evaluation script in the container.

        Args:
            test_output_path (Path): Path to the test output file.
            timeout (int): Maximum time to wait for the evaluation to complete, in seconds.
        """
        pass

    @abstractmethod
    def kill(self):
        """
        Kill the container.
        """
        pass
