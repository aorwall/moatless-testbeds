import logging
from abc import ABC, abstractmethod
from collections import namedtuple

from testbeds.schema import CommandExecutionResponse

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
    def execute(
        self, commands: list[str], timeout: int = 60
    ) -> CommandExecutionResponse:
        """
        Execute a list of commands in the container.

        Args:
            commands (list[str]): List of commands to execute.
            timeout (int): Maximum time to wait for execution to complete, in seconds.

        Returns:
            CommandExecutionResponse: Response containing execution details.
        """
        pass

    @abstractmethod
    def get_execution_status(self) -> CommandExecutionResponse:
        """
        Get the status of currently executed command.

        Returns:
            CommandExecutionResponse: Response containing execution status and details.
        """
        pass

    @abstractmethod
    def is_executing(self) -> bool:
        """
        Check if the container is currently executing a command.

        Returns:
            bool: True if a command is being executed, False otherwise.
        """
        pass

    @abstractmethod
    def get_output(self) -> str:
        """
        Get the output of the last executed command.

        Returns:
            str: Output of the last executed command.
        """
        pass

    @abstractmethod
    def write_file(self, file_path: str, content: bytes):
        """
        Write content to a file in the container.

        Args:
            file_path (str): Path to the file in the container.
            content (bytes): Content to write to the file.
        """
        pass

    @abstractmethod
    def read_file(self, file_path: str) -> str:
        """
        Read content from a file in the container.

        Args:
            file_path (str): Path to the file in the container.

        Returns:
            str: Content of the file.
        """
        pass

    @abstractmethod
    def kill(self):
        """
        Kill the container.
        """
        pass
