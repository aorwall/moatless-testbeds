import requests
import json
import logging

from testbed.schema import EvaluationResult, Prediction

logger = logging.getLogger(__name__)


class TestbedClient:
    def __init__(self, testbed_id: str, host: str = "localhost", port: int = 8000):
        self.testbed_id = testbed_id
        self.base_url = f"http://{host}:{port}"

    def check_health(self, timeout: int = 30):
        try:
            response = requests.get(f"{self.base_url}/health", timeout=timeout)
            response.raise_for_status()
            data = response.json()
            return data.get("status") == "OK"
        except requests.RequestException as e:
            logger.error(f"Error during ping: {str(e)}")
            return False

    def run_evaluation(self, prediction: Prediction) -> EvaluationResult:
        try:
            response = requests.post(
                f"{self.base_url}/run_evaluation", json=prediction.model_dump()
            )
            response.raise_for_status()
            return EvaluationResult.model_validate(response.json())
        except requests.RequestException as e:
            logger.error(f"Error during run_evaluation: {str(e)}")
            return None
