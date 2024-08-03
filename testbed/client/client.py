import os
import json
import logging
import time
import zmq

from testbed.client.comm.zeromq_communicator import ZeroMQCommunicator
from testbed.client.comm.communicator import Message
from testbed.schema import Prediction
from testbed.storage.azure_blob import AzureBlobStorage

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


class TestbedClient:
    def __init__(self, testbed_id: str, pubsub_address: str, req_address: str):
        self.communicator = ZeroMQCommunicator(testbed_id=testbed_id, pubsub_address=pubsub_address, req_address=req_address, is_server=False)
        self.storage = AzureBlobStorage()

    def __enter__(self):
        self.communicator.__enter__()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.communicator.__exit__(exc_type, exc_val, exc_tb)

    def close(self):
        self.__exit__(None, None, None)

    def ping(self, timeout=30):
        logger.info(f"Sending ping request with timeout {timeout} seconds")
        try:
            response = self.communicator.send_request("ping", {}, timeout * 1000)
            logger.info(f"Received ping response: {response.type}")
            return response.type == "pong"
        except zmq.ZMQError as e:
            logger.error(f"ZMQ error during ping: {str(e)}")
            return False
        except TimeoutError:
            logger.error("Ping request timed out")
            return False

    def run_evaluation(self, prediction: Prediction):
        self.storage.store_prediction(prediction=prediction, remote_dir=prediction.run_id)
        response = self.communicator.send_request("run_prediction", {"run_id": prediction.run_id})
        if response.type != "evaluation_started":
            raise Exception(f"Unexpected response: {response.type}")
        return response.body["run_id"]

    def wait_for_evaluation_result(self, timeout=3600):
        start_time = time.time()
        while time.time() - start_time < timeout:
            message = self.communicator.receive_message()
            if message is None:
                continue
            if message.type == "evaluation_finished":
                return message.body
            elif message.type == "evaluation_error":
                raise Exception(f"Evaluation error: {message.body['error']}")
            time.sleep(0.1)
        raise TimeoutError("Evaluation result not received within the specified timeout")
