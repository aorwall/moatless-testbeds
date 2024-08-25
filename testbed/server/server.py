import base64
import os
import time

from flask import Flask, request, jsonify, send_file
import logging

from testbed.schema import RunEvaluationRequest
from testbed.server.testbed import Testbed
from kubernetes import config as k8s_config
from functools import lru_cache

logger = logging.getLogger(__name__)


def create_app():
    app = Flask(__name__)

    # Log Kubernetes configuration
    try:
        k8s_config.load_incluster_config()
        logger.info("Loaded in-cluster Kubernetes configuration")
    except k8s_config.ConfigException:
        try:
            k8s_config.load_kube_config()
            logger.info("Loaded Kubernetes configuration from default location")
        except k8s_config.ConfigException:
            logger.error("Could not load Kubernetes configuration")

    testbed = Testbed(os.getenv("TESTBED_ID", "test_testbed"))

    @lru_cache(maxsize=1)
    def check_container_reachability():
        return testbed.container.is_reachable()

    logger.info("Check container reachability")
    check_container_reachability()
    logger.info("Container reachability checked")

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

    # Invalidate cache every 60 seconds
    @app.before_request
    def clear_cache():
        if time.time() - clear_cache.last_cleared > 60:
            check_container_reachability.cache_clear()
            clear_cache.last_cleared = time.time()

    clear_cache.last_cleared = time.time()

    @app.route("/run_evaluation", methods=["POST"])
    def run_evaluation():
        logger.info("run_evaluation() Run evaluation requested")
        start_time = time.time()
        data = request.json
        run_eval_request = RunEvaluationRequest.model_validate(data)
        try:
            logger.debug("Starting evaluation")
            result = testbed.run_evaluation(
                run_id=run_eval_request.run_id,
                instance=run_eval_request.instance,
                patch=run_eval_request.patch
            )
            logger.info(f"run_evaluation() Evaluation completed in {time.time() - start_time:.2f} seconds")
            return jsonify(result.model_dump())
        except Exception as e:
            logger.exception(f"run_evaluation() Error during evaluation after {time.time() - start_time:.2f} seconds")
            return jsonify({"error": str(e)}), 500

    @app.route("/commands", methods=["POST"])
    def execute_command():
        data = request.json
        commands = data.get("commands")
        logger.info(f"execute_command() {commands}")

        if not commands:
            return jsonify({"error": "Missing commands"}), 400

        try:
            testbed.run_commands(commands)
            return jsonify({"message": "Commands executed"}), 200
        except Exception as e:
            logger.exception(f"run() Error during run")
            return jsonify({"error": str(e)}), 500

    @app.route("/commands", methods=["GET"])
    def execution_status():
        logger.info("execution_status()")
        try:
            result = testbed.get_run_status()
            return jsonify(result.model_dump())
        except Exception as e:
            logger.exception(f"run() Error during run")
            return jsonify({"error": str(e)}), 500

    @app.route("/file", methods=["GET"])
    def get_file():
        file_path = request.args.get('file_path')
        logger.info(f"get_file() Reading file: {file_path}")
        if not file_path:
            return jsonify({"error": "Missing file_path parameter"}), 400
        
        full_path = os.path.join('/testbed', file_path)
        try:
            with open(full_path, 'rb') as file:
                content = file.read()
            encoded_content = base64.b64encode(content).decode()
            return jsonify({"content": encoded_content}), 200
        except FileNotFoundError:
            logger.warning(f"File not found: {full_path}")
            return jsonify({"error": f"File not found: {file_path}"}), 404
        except Exception as e:
            logger.exception(f"Error reading file: {file_path}")
            return jsonify({"error": f"Error reading file: {str(e)}"}), 500

    @app.route("/file", methods=["POST"])
    def save_file():
        data = request.json
        file_path = data.get('file_path')
        content = data.get('content')
        logger.info(f"save_file() Saving file: {file_path}")
        if not file_path or not content:
            return jsonify({"error": "Missing file_path or content"}), 400
        
        full_path = os.path.join('/testbed', file_path)
        try:
            os.makedirs(os.path.dirname(full_path), exist_ok=True)
            decoded_content = base64.b64decode(content)
            with open(full_path, 'wb') as file:
                file.write(decoded_content)
            return jsonify({"message": f"File saved successfully: {file_path}"}), 200
        except Exception as e:
            logger.exception(f"Error saving file: {file_path}")
            return jsonify({"error": f"Error saving file: {str(e)}"}), 500

    return app


if __name__ == "__main__":
    app = create_app()
    app.run(host="0.0.0.0", port=8000)