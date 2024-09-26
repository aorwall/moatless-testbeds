import base64
import os
import time
import uuid

from flask import Flask, request, jsonify, send_file
import logging

from testbed.container.kubernetes import KubernetesContainer
from testbed.schema import (
    RunCommandsRequest,
)

logger = logging.getLogger(__name__)


def create_app():
    app = Flask(__name__)

    container = KubernetesContainer()

    def check_container_reachability():
        while not container.is_reachable():
            time.sleep(0.1)

        return True

    check_container_reachability()

    @app.route("/health", methods=["GET"])
    def health():
        logger.debug(f"health() Health check from {request.remote_addr}")
        try:
            if check_container_reachability():
                logger.debug("health() status OK")
                return jsonify({"status": "OK"}), 200
            else:
                logger.warning("health() Container is not reachable")
                return jsonify(
                    {"status": "ERROR", "message": "Testbed container is not reachable"}
                ), 500
        except Exception as e:
            error_id = str(uuid.uuid4())
            logger.exception(f"health() Error checking container reachability, error_id: {error_id}")
            return jsonify(
                {
                    "status": "ERROR",
                    "message": f"Error checking container reachability: {str(e)}",
                    "error_id": error_id,
                }
            ), 500

    @app.route("/exec", methods=["POST"])
    def execute_command():
        data = request.json
        run_request = RunCommandsRequest(**data)
        logger.info(f"execute_command() {run_request.commands}")

        try:
            result = container.execute(run_request.commands, run_request.timeout)
            return jsonify(result.model_dump()), 200
        except Exception as e:
            error_id = str(uuid.uuid4())
            logger.exception(f"execute_command() Error during execution, error_id: {error_id}")
            return jsonify({"status": "ERROR", "message": f"Error during execution: {str(e)}", "error_id": error_id}), 500

    @app.route("/exec", methods=["GET"])
    def get_execution_status():
        try:
            result = container.get_execution_status()
            return jsonify(result.model_dump()), 200
        except Exception as e:
            error_id = str(uuid.uuid4())
            logger.exception(f"get_execution_status() Error retrieving status, error_id: {error_id}")
            return jsonify({"status": "ERROR", "message": f"Error retrieving status: {str(e)}", "error_id": error_id}), 500

    @app.route("/file", methods=["GET"])
    def get_file():
        file_path = request.args.get("file_path")
        logger.info(f"get_file() Reading file: {file_path}")
        if not file_path:
            return jsonify({"status": "ERROR", "message": "Missing file_path parameter"}), 400

        try:
            content = container.read_file(file_path)
            encoded_content = base64.b64encode(content.encode()).decode()
            return jsonify({"status": "OK", "content": encoded_content}), 200
        except FileNotFoundError as e:
            error_id = str(uuid.uuid4())
            logger.error(f"get_file() File not found: {file_path}, error_id: {error_id}")
            return jsonify({"status": "ERROR", "message": f"File not found: {str(e)}", "error_id": error_id}), 404
        except Exception as e:
            error_id = str(uuid.uuid4())
            logger.exception(f"Error reading file: {file_path}, error_id: {error_id}")
            return jsonify({"status": "ERROR", "message": f"Error reading file: {str(e)}", "error_id": error_id}), 500

    @app.route("/file", methods=["POST"])
    def save_file():
        data = request.json
        file_path = data.get("file_path")
        content = data.get("content")
        logger.info(f"save_file() Saving file: {file_path}")
        if not file_path or not content:
            return jsonify({"status": "ERROR", "message": "Missing file_path or content"}), 400

        try:
            decoded_content = base64.b64decode(content)
            container.write_file(file_path, decoded_content)
            return jsonify({"status": "OK", "message": f"File saved successfully: {file_path}"}), 200
        except Exception as e:
            error_id = str(uuid.uuid4())
            logger.exception(f"Error saving file: {file_path}, error_id: {error_id}")
            return jsonify({"status": "ERROR", "message": f"Error saving file: {str(e)}", "error_id": error_id}), 500

    return app


if __name__ == "__main__":
    app = create_app()
    app.run(host="0.0.0.0", port=8000)
