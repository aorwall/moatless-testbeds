import logging
from abc import abstractmethod, ABC

logger = logging.getLogger(__name__)


class Events(ABC):

    @abstractmethod
    def send_start_event(self, run_id: str, instance_id: str):
        pass

    @abstractmethod
    def send_finish_event(self, run_id: str, instance_id: str, status: str):
        pass
