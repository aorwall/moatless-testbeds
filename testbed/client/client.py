import requests
import json
import logging
import base64

from testbed.schema import EvaluationResult, Prediction, RunEvaluationRequest, CommandStatusResponse

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

    def execute_commands(self, commands: list[str]):
        try:
            response = requests.post(f"{self.base_url}/commands", json={"commands": commands})
            response.raise_for_status()
        except requests.RequestException as e:
            logger.error(f"Error during execute_commands: {str(e)}")
            return {"error": str(e)}

    def get_execution(self) -> CommandStatusResponse:
        try:
            response = requests.get(f"{self.base_url}/commands")
            response.raise_for_status()
            return CommandStatusResponse.model_validate(response.json())
        except requests.RequestException as e:
            logger.error(f"Error during get_execution_status: {str(e)}")
            raise e

    def run_evaluation(self, request: RunEvaluationRequest) -> EvaluationResult:
        try:
            response = requests.post(
                f"{self.base_url}/run_evaluation", json=request.model_dump()
            )
            response.raise_for_status()
            return EvaluationResult.model_validate(response.json())
        except requests.RequestException as e:
            logger.error(f"Error during run_evaluation: {str(e)}")
            return None

    def save_file(self, file_path: str, content: str):
        try:
            encoded_content = base64.b64encode(content.encode()).decode()
            data = {
                'file_path': file_path,
                'content': encoded_content
            }
            response = requests.post(f"{self.base_url}/file", json=data)
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            logger.error(f"Error saving file: {str(e)}")
            return {"error": str(e)}

    def get_file(self, file_path: str):
        try:
            params = {'file_path': file_path}
            response = requests.get(f"{self.base_url}/file", params=params)
            response.raise_for_status()
            data = response.json()
            if 'content' in data:
                return base64.b64decode(data['content']).decode()
            else:
                return data
        except requests.RequestException as e:
            logger.error(f"Error getting file: {str(e)}")
            return {"error": str(e)}
