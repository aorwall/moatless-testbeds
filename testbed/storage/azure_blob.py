import json
import logging
import os
from pathlib import Path

from azure.core.exceptions import ResourceNotFoundError
from azure.storage.blob import BlobServiceClient

from testbed.schema import Prediction
from testbed.storage import Storage

logger = logging.getLogger(__name__)


class AzureBlobStorage(Storage):

    def __init__(self):
        self.container_name = os.getenv("AZURE_BLOB_CONTAINER_NAME", "evaluations")
        if not os.environ.get("AZURE_STORAGE_CONNECTION_STRING"):
            raise ValueError("AZURE_STORAGE_CONNECTION_STRING is not set.")

        self.blob_service_client = BlobServiceClient.from_connection_string(os.environ.get("AZURE_STORAGE_CONNECTION_STRING"))

    def store_dir(self, local_dir: Path, remote_dir: str):
        container_client = self.blob_service_client.get_container_client(self.container_name)
        for file in os.listdir(local_dir):
            item_path = os.path.join(local_dir, file)
            if os.path.isfile(item_path) and not os.path.islink(item_path):
                blob_name = f"{remote_dir}/{file}"
                blob_client = container_client.get_blob_client(blob_name)
                logger.info(f"Uploading {item_path} to {blob_name}")
                with open(item_path, "rb") as data:
                    blob_client.upload_blob(data, overwrite=True)

    def store_prediction(self, prediction: Prediction, remote_dir: str):
        container_client = self.blob_service_client.get_container_client(self.container_name)
        blob_name = f"{remote_dir}/prediction.json"
        blob_client = container_client.get_blob_client(blob_name)
        logger.info(f"Uploading prediction to {blob_name}")
        blob_client.upload_blob(json.dumps(prediction.model_dump()), overwrite=True)

    def fetch_prediction(self, remote_dir: str) -> Prediction | None:
        container_client = self.blob_service_client.get_container_client(self.container_name)
        blob_name = f"{remote_dir}/prediction.json"
        blob_client = container_client.get_blob_client(blob_name)
        logger.info(f"Downloading prediction from {blob_name}")
        try:
            prediction = json.loads(blob_client.download_blob().readall())
            return Prediction.model_validate(prediction)
        except ResourceNotFoundError:
            logger.info(f"Prediction not found at {blob_name}")
            return None
        except Exception as e:
            logger.error(f"Error fetching prediction from {blob_name}")
            raise
