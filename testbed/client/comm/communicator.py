from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Iterable


@dataclass
class Message:
    type: str
    body: dict[str, Any]
    id: str | None = None


class Communicator(ABC):

    def __init__(self, testbed_id: str):
        self._testbed_id = testbed_id

    @property
    def testbed_id(self):
        return self._testbed_id


    @abstractmethod
    def receive_message(self) -> Message | None:
        """
        Receive messages from the communication service.

        Returns:
            Message | None: The received message or None if no message is received.
        """
        pass

    @abstractmethod
    def publish_message(self, message_type: str, data: dict[str, Any]) -> None:
        """
        Send a message to the communication service.

        Args:
            message_type (str): The type of the message to send.
            data (dict[str, Any]): The data of the message to send.
        """
        pass

    @abstractmethod
    def send_request(self, message_type: str, data: dict[str, Any], timeout: int = 5000) -> Message:
        """
        Send a request to the communication service.

        Args:
            message_type (str): The type of the message to send.
            data (dict[str, Any]): The data of the message to send.
            timeout (int): The timeout for the request in milliseconds.

        Returns:
            Message: The received message.
        """
        pass
