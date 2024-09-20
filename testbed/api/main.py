import base64
import os
import time
import json
from testbed.client.manager import TestbedManager

from flask import Flask, request, jsonify, send_file
from functools import wraps
import logging
from werkzeug.exceptions import HTTPException
from werkzeug.exceptions import RequestTimeout

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)

def load_api_keys():
    api_keys_path = os.environ.get('API_KEYS_PATH', '/app/api_keys.json')
    try:
        with open(api_keys_path, 'r') as f:
            return json.load(f)
    except FileNotFoundError:
        logger.error(f"API keys file not found at {api_keys_path}")
        return {}
    except json.JSONDecodeError:
        logger.error(f"Failed to parse API keys JSON from {api_keys_path}")
        return {}

def create_app():
    app = Flask(__name__)
    app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 0  # Disable caching
    app.config['PROPAGATE_EXCEPTIONS'] = True
    testbed_manager = TestbedManager(in_cluster=True)
    api_keys = load_api_keys()

    def validate_api_key(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            api_key = request.headers.get('X-API-Key')
            user_id = api_keys.get(api_key)
            if not user_id:
                return jsonify({"error": "Invalid API key"}), 401
            return f(user_id=user_id, *args, **kwargs)
        return decorated_function

    @app.errorhandler(HTTPException)
    def handle_exception(e):
        response = e.get_response()
        response.data = json.dumps({
            "code": e.code,
            "name": e.name,
            "description": e.description,
        })
        response.content_type = "application/json"
        return response

    @app.errorhandler(TimeoutError)
    def handle_timeout_error(e):
        return jsonify({"error": "Request timed out"}), 504

    @app.route("/health", methods=["GET"])
    def health_check():
        return jsonify({"status": "healthy"}), 200

    @app.route("/testbeds", methods=["GET"])
    @validate_api_key
    def list_testbeds(user_id):
        return jsonify(testbed_manager.list_testbeds(user_id)), 200

    @app.route("/testbeds", methods=["POST"])
    @validate_api_key
    def get_or_create_testbed(user_id):
        data = request.json
        logger.info(f"get_or_create_testbed(user_id={user_id}, data={data})")
        instance_id = data.get("instance_id")
        if not instance_id:
            return jsonify({"error": "Missing instance_id parameter"}), 400

        testbed = testbed_manager.get_or_create_testbed(instance_id, user_id)
        logger.info(f"Testbed created or retrieved: id={testbed.testbed_id}, user_id={user_id}, instance_id={instance_id}")
        return jsonify(testbed.model_dump()), 200

    @app.route("/testbeds/<testbed_id>", methods=["GET"])
    @validate_api_key
    def get_testbed(testbed_id, user_id: str):
        testbed = testbed_manager.get_testbed(testbed_id, user_id)
        if not testbed:
            logger.warning(f"Testbed not found: id={testbed_id}, user_id={user_id}")
            return jsonify({"error": "Testbed not found"}), 404
        
        return jsonify(testbed.model_dump()), 200

    @app.route("/testbeds/<testbed_id>", methods=["DELETE"])
    @validate_api_key
    def delete_testbed(testbed_id: str, user_id: str):
        logger.info(f"delete_testbed(testbed_id={testbed_id}, user_id={user_id})")
        testbed_manager.delete_testbed(testbed_id, user_id)
        logger.info(f"Testbed deleted: id={testbed_id}, user_id={user_id}")
        return jsonify({"message": "Testbed killed"}), 200

    @app.route("/testbeds/<testbed_id>/run-tests", methods=["POST"])
    @validate_api_key
    def run_tests(testbed_id, user_id: str):
        data = request.json
        test_files = data.get("test_files")
        patch = data.get("patch")
        logger.debug(f"run_tests(testbed_id={testbed_id}, user_id={user_id})")
        client = testbed_manager.create_client(testbed_id, user_id=user_id)
        client.wait_until_ready(timeout=600)

        try:
            result = client.run_tests(test_files, patch)
            return jsonify(result.model_dump()), 200
        except TimeoutError:
            logger.exception(f"run_tests() Timeout during execution")
            return jsonify({"error": "Request timed out"}), 504
        except Exception as e:
            logger.exception(f"run_tests() Error during execution")
            return jsonify({"error": str(e)}), 500

    @app.route("/testbeds/<testbed_id>/run-evaluation", methods=["POST"])
    @validate_api_key
    def run_evaluation(testbed_id: str, user_id: str):
        data = request.json
        patch = data.get("patch")
        logger.debug(f"run_evaluation(testbed_id={testbed_id}, user_id={user_id})")

        client = testbed_manager.create_client(testbed_id, user_id=user_id)
        client.wait_until_ready(timeout=600)

        try:
            result = client.run_evaluation(patch=patch)
            return jsonify(result.model_dump()), 200
        except Exception as e:
            logger.exception(f"run_evaluation() Error during execution")
            return jsonify({"error": str(e)}), 500

    @app.route("/testbeds", methods=["DELETE"])
    @validate_api_key
    def delete_all_testbeds(user_id: str):
        try:
            logger.info(f"delete_all_testbeds(user_id={user_id})")
            deleted_count = testbed_manager.delete_all_testbeds(user_id)
            logger.info(f"Deleted {deleted_count} testbeds for user {user_id}")
            return jsonify({"message": f"Deleted {deleted_count} testbeds"}), 200
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/cleanup", methods=["POST"])
    @validate_api_key
    def cleanup_user_resources(user_id: str):
        try:
            logger.info(f"cleanup_user_resources(user_id={user_id})")
            deleted_count = testbed_manager.cleanup_user_resources(user_id)
            logger.info(f"Cleaned up {deleted_count} resources for user {user_id}")
            return jsonify({"message": f"Cleaned up {deleted_count} resources"}), 200
        except Exception as e:
            logger.exception(f"Error during cleanup for user {user_id}")
            return jsonify({"error": str(e)}), 500

    return app


if __name__ == "__main__":
    app = create_app()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8000)), threaded=True)
