import base64
import os
import time

from flask import Flask, request, jsonify, send_file
import logging

from testbed.container.kubernetes import KubernetesContainer
from testbed.schema import (
    RunEvaluationRequest,
    RunCommandsRequest,
    GetExecutionResultRequest,
)
from functools import lru_cache

logger = logging.getLogger(__name__)


def create_app():
    app = Flask(__name__)

    container = KubernetesContainer()

    @lru_cache(maxsize=1)
    def check_container_reachability():
        return container.is_reachable()

    # Invalidate cache every 60 seconds
    @app.before_request
    def clear_cache():
        if time.time() - clear_cache.last_cleared > 60:
            check_container_reachability.cache_clear()
            clear_cache.last_cleared = time.time()

    clear_cache.last_cleared = time.time()

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
            logger.exception("health() Error checking container reachability")
            return jsonify(
                {
                    "status": "ERROR",
                    "message": f"Error checking container reachability: {str(e)}",
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
            logger.exception(f"execute_command() Error during execution")
            return jsonify({"error": str(e)}), 500

    @app.route("/exec/<execution_id>", methods=["GET"])
    def get_execution_status(execution_id: str):
        try:
            result = container.get_execution_status(execution_id)
            return jsonify(result.model_dump()), 200
        except Exception as e:
            logger.exception(f"get_execution_status() Error retrieving status")
            return jsonify({"error": str(e)}), 500

    @app.route("/exec", methods=["GET"])
    def list_executed_commands():
        try:
            result = container.list_executed_commands()
            return jsonify([summary.model_dump() for summary in result]), 200
        except Exception as e:
            logger.exception(f"list_executed_commands() Error listing commands")
            return jsonify({"error": str(e)}), 500

    @app.route("/file", methods=["GET"])
    def get_file():
        file_path = request.args.get("file_path")
        logger.info(f"get_file() Reading file: {file_path}")
        if not file_path:
            return jsonify({"error": "Missing file_path parameter"}), 400

        try:
            content = container.read_file(file_path)
            encoded_content = base64.b64encode(content.encode()).decode()
            return jsonify({"content": encoded_content}), 200
        except Exception as e:
            logger.exception(f"Error reading file: {file_path}")
            return jsonify({"error": f"Error reading file: {str(e)}"}), 500

    @app.route("/file", methods=["POST"])
    def save_file():
        data = request.json
        file_path = data.get("file_path")
        content = data.get("content")
        logger.info(f"save_file() Saving file: {file_path}")
        if not file_path or not content:
            return jsonify({"error": "Missing file_path or content"}), 400

        try:
            decoded_content = base64.b64decode(content)
            container.write_file(file_path, decoded_content)
            return jsonify({"message": f"File saved successfully: {file_path}"}), 200
        except Exception as e:
            logger.exception(f"Error saving file: {file_path}")
            return jsonify({"error": f"Error saving file: {str(e)}"}), 500

    return app


if __name__ == "__main__":
    app = create_app()
    app.run(host="0.0.0.0", port=8000)
