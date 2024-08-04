import logging
import os
from abc import ABC, abstractmethod
from pathlib import Path

from azure.storage.blob import BlobServiceClient

from testbed.schema import Prediction

logger = logging.getLogger(__name__)


class Storage(ABC):
    @abstractmethod
    def store_dir(self, local_dir: str, remote_dir: str):
        pass

    @abstractmethod
    def store_prediction(self, prediction: Prediction, remote_dir: str):
        pass

    @abstractmethod
    def fetch_prediction(self, remote_dir: str) -> Prediction | None:
        pass
