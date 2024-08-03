import os
import json
import logging
import zmq
import threading
import time
from testbed.client.comm.zeromq_communicator import ZeroMQCommunicator
from testbed.client.comm.communicator import Message
from testbed.server.testbed import Testbed
from testbed.schema import EvaluationResult
import websockets

logger = logging.getLogger(__name__)

class Server:

    def __init__(self):
        self.testbed_id = os.getenv("TESTBED_ID", "test_testbed")
        pubsub_port = os.getenv("ZEROMQ_PUBSUB_PORT", 5556)
        req_port = os.getenv("ZEROMQ_REQ_PORT", 5557)
        self.pubsub_address = f"tcp://*:{pubsub_port}"
        self.req_address = f"tcp://*:{req_port}"
        self.communicator = ZeroMQCommunicator(testbed_id=self.testbed_id, pubsub_address=self.pubsub_address, req_address=self.req_address, is_server=True)
        self.testbed = Testbed(self.testbed_id)
        self.running = False
        self.clients = set()

        logger.info(f"Server initialized with testbed_id: {self.testbed_id}, pubsub_address: {self.pubsub_address}, req_address: {self.req_address}")

    async def start_server(self):
        server = await websockets.serve(self.handle_client, '0.0.0.0', 8765)
        logger.info(f"WebSocket server started on 0.0.0.0:8765")
        await server.wait_closed()

    def run(self):
        if not self.testbed.container.is_reachable(timeout=30):
            logger.error("Container is not reachable.")
            raise Exception("Container is not reachable.")

        self.running = True
        with self.communicator:
            while self.running:
                self.communicator.handle_requests(self.process_message, timeout=100)

    def stop(self):
        self.running = False

    def process_message(self, message):
        try:
            logger.info(f"Received message: {message}")
            if message.type == "ping":
                return Message("pong", {})
            elif message.type == "run_prediction":
                body = message.body
                run_id = body.get("run_id")
                if run_id:
                    # Start evaluation in a separate thread
                    threading.Thread(target=self.run_evaluation, args=(run_id,)).start()
                    return Message("evaluation_started", {"run_id": run_id})
                else:
                    logger.error("Missing run_id in run_prediction message.")
                    return Message("error", {"message": "Missing run_id"})
            else:
                logger.warning(f"Unknown message type: {message.type}")
                return Message("error", {"message": f"Unknown message type: {message.type}"})
        except Exception as e:
            logger.error(f"Error processing message: {str(e)}")
            return Message("error", {"message": str(e)})
    
    def run_evaluation(self, run_id):
        try:
            result = self.testbed.run_evaluation(run_id)
            self.communicator.publish_message("evaluation_finished", {"run_id": run_id, "result": result.model_dump(exclude_none=True, exclude_unset=True)})
        except Exception as e:
            logger.exception(f"Error running evaluation")
            self.communicator.publish_message("evaluation_error", {"run_id": run_id, "error": str(e)})
