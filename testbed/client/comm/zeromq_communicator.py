import json
import logging
import time
from typing import Any, Iterable
import zmq
from zmq.auth.thread import ThreadAuthenticator

from testbed.client.comm.communicator import Communicator, Message

logger = logging.getLogger(__name__)

class ZeroMQCommunicator(Communicator):
    def __init__(self, testbed_id: str, pubsub_address: str, req_address: str, is_server: bool = False):
        super().__init__(testbed_id)
        self.pubsub_address = pubsub_address
        self.req_address = req_address
        self.is_server = is_server
        self.context = zmq.Context()
        self.pubsub_socket = None
        self.req_socket = None
        self.poller = zmq.Poller()

    def __enter__(self):
        self.setup_sockets()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.cleanup()

    def setup_sockets(self):
        if self.is_server:
            self.pubsub_socket = self.context.socket(zmq.PUB)
            self.pubsub_socket.bind(self.pubsub_address)
            self.req_socket = self.context.socket(zmq.REP)
            self.req_socket.bind(self.req_address)
            logger.info("Server sockets bound successfully")
        else:
            self.pubsub_socket = self.context.socket(zmq.SUB)
            self.pubsub_socket.connect(self.pubsub_address)
            self.pubsub_socket.setsockopt_string(zmq.SUBSCRIBE, self.testbed_id)
            self.req_socket = self.context.socket(zmq.REQ)
            self.req_socket.connect(self.req_address)
            if not self.verify_connection():
                raise ConnectionError("Failed to connect to server")
            logger.info("Client sockets connected successfully")
        
        self.poller.register(self.req_socket, zmq.POLLIN)

    def cleanup(self):
        if self.pubsub_socket:
            self.pubsub_socket.close()
        if self.req_socket:
            self.req_socket.close()
        self.context.term()

    def verify_connection(self, max_retries=5, retry_delay=2):
        logger.info(f"Verifying connection to {self.req_address}")
        if not self.is_server:
            for attempt in range(max_retries):
                try:
                    self.send_request("ping", {}, timeout=5000)
                    logger.info(f"Connection verified on attempt {attempt + 1}")
                    return True
                except Exception as e:
                    logger.info(f"Connection attempt {attempt + 1} failed: {str(e)}")
                    if attempt < max_retries - 1:
                        logger.info(f"Retrying in {retry_delay} seconds...")
                        time.sleep(retry_delay)
                        self.reset_req_socket()
            
            logger.error(f"Connection verification failed after {max_retries} attempts")
            return False
        return True

    def reset_req_socket(self):
        self.poller.unregister(self.req_socket)
        self.req_socket.close()
        self.req_socket = self.context.socket(zmq.REQ)
        self.req_socket.connect(self.req_address)
        self.poller.register(self.req_socket, zmq.POLLIN)

    def check_socket_state(self):
        if not self.pubsub_socket or self.pubsub_socket.closed:
            logger.warning("PubSub socket is not initialized or closed")
            return False
        if not self.req_socket or self.req_socket.closed:
            logger.warning("REQ/REP socket is not initialized or closed")
            return False
        return True

    def receive_message(self) -> Message | None:
        try:
            if not self.pubsub_socket.poll(timeout=100):
                return None
            testbed_id, message_type, data = self.pubsub_socket.recv_multipart()
            if testbed_id.decode() == self.testbed_id:
                return Message(
                    message_type.decode(),
                    json.loads(data.decode())
                )
        except zmq.ZMQError as e:
            logger.error(f"ZMQ error while receiving: {str(e)}")
            return None

    def publish_message(self, message_type: str, data: dict[str, Any]) -> None:
        if not self.check_socket_state():
            logger.error("Cannot publish message: sockets not ready")
            return

        logger.debug(f"Sending message of type: {message_type}")
        try:
            self.pubsub_socket.send_multipart([
                self.testbed_id.encode(),
                message_type.encode(),
                json.dumps(data).encode()
            ])
        except zmq.ZMQError as e:
            logger.error(f"ZMQ error while sending: {str(e)}")

    def send_request(self, message_type: str, data: dict[str, Any], timeout: int = 5000) -> Message:
        if not self.check_socket_state():
            raise RuntimeError("Cannot send request: sockets not ready")

        logger.info(f"Sending request of type: {message_type} to {self.req_address}")
        try:
            self.req_socket.send_multipart([message_type.encode(), json.dumps(data).encode()])
            
            socks = dict(self.poller.poll(timeout))
            if self.req_socket in socks and socks[self.req_socket] == zmq.POLLIN:
                response_type, response_data = self.req_socket.recv_multipart()
                logger.debug(f"Received response of type: {response_type.decode()}")
                return Message(
                    response_type.decode(),
                    json.loads(response_data.decode())
                )
            else:
                raise TimeoutError("No response received within the specified timeout")
        except zmq.ZMQError as e:
            logger.error(f"ZMQ error while sending request: {str(e)}")
            self.reset_req_socket()
            raise
        except Exception as e:
            logger.error(f"Unexpected error in send_request: {str(e)}")
            raise

    def handle_requests(self, handler_func, timeout=100):
        if not self.check_socket_state():
            logger.error("Cannot handle requests: sockets not ready")
            return False

        if not self.is_server:
            raise ValueError("handle_requests can only be called on the server side")
        
        try:
            if self.req_socket.poll(timeout=timeout):
                request_type, request_data = self.req_socket.recv_multipart()
                request = Message(
                    request_type.decode(),
                    json.loads(request_data.decode())
                )
                logger.debug(f"Received request of type: {request.type}")
                
                response = handler_func(request)
                
                self.req_socket.send_multipart([
                    response.type.encode(),
                    json.dumps(response.body).encode()
                ])
                logger.debug(f"Sent response of type: {response.type}")
                return True
            else:
                return False
        except zmq.ZMQError as e:
            logger.error(f"ZMQ error while handling requests: {str(e)}")
        except Exception as e:
            logger.error(f"Error while handling request: {str(e)}")
            # Send error response
            error_response = Message("error", {"error": str(e)})
            self.req_socket.send_multipart([
                error_response.type.encode(),
                json.dumps(error_response.body).encode()
            ])
        return False

    def __del__(self):
        self.__exit__(None, None, None)