import os
import time

from flask import Flask, request, jsonify
import logging
from testbed.server.testbed import Testbed
from testbed.schema import Prediction
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
        prediction = Prediction.model_validate(data)
        if not prediction:
            logger.warning("run_evaluation() Missing prediction in request")
            return jsonify({"error": "Missing prediction"}), 400

        try:
            logger.debug("Starting evaluation")
            result = testbed.run_evaluation(prediction)
            logger.info(f"run_evaluation() Evaluation completed in {time.time() - start_time:.2f} seconds")
            return jsonify(result.model_dump())
        except Exception as e:
            logger.exception(f"run_evaluation() Error during evaluation after {time.time() - start_time:.2f} seconds")
            return jsonify({"error": str(e)}), 500

    return app


app = create_app()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)