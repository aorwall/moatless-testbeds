import logging
import os

import requests


logger = logging.getLogger(__name__)


class EvalAPI:

    def __init__(self):
        self.base_url = os.environ.get("API_ENDPOINT")
        logger.info(f"API endpoint: {self.base_url}")

    def fetch_prediction(self, prediction_id: str) -> dict:
        endpoint = f"{self.base_url}/runner/predictions/{prediction_id}"
        response = requests.get(endpoint)

        if response.status_code != 200:
            logger.error(
                f"Request to {endpoint} failed.\nStatus code: {response.status_code}\nContent: {response.text}")
            raise Exception("Failed to fetch prediction")

        try:
            return response.json()
        except Exception as e:
            logger.error(f"Failed to parse response from {endpoint}.\n{e}")
            raise Exception("Failed to fetch prediction")