import base64
import os
import time
import json
from testbed.client.manager import TestbedManager
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.trace.sampling import TraceIdRatioBased
from opentelemetry.sdk.resources import Resource
from opentelemetry.semconv.resource import ResourceAttributes
from opentelemetry.instrumentation.flask import FlaskInstrumentor
from azure.monitor.opentelemetry import configure_azure_monitor
from azure.monitor.opentelemetry.exporter import ApplicationInsightsSampler
from opentelemetry.propagate import inject

from flask import Flask, request, jsonify, send_file
from functools import wraps
import logging
from werkzeug.exceptions import HTTPException
from werkzeug.exceptions import RequestTimeout
import uuid
from kubernetes import client as k8s_client
import requests

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

def configure_opentelemetry(app):
    custom_sampler = ApplicationInsightsSampler(
        sampling_ratio=0.1,  # 10% sampling rate
    )
    
    tracer_provider = TracerProvider(sampler=custom_sampler)

    configure_azure_monitor(
        tracer_provider=tracer_provider,
    )
   
    FlaskInstrumentor().instrument_app(
        app,
        excluded_urls="health"
    )

def create_app():
    app = Flask(__name__)
    app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 0  # Disable caching
    app.config['PROPAGATE_EXCEPTIONS'] = True
    testbed_manager = TestbedManager(in_cluster=True)
    api_keys = load_api_keys()

    configure_opentelemetry(app)

    def validate_api_key(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            api_key = request.headers.get('X-API-Key')
            user_id = api_keys.get(api_key)
            if not user_id:
                return jsonify({"error": "Invalid API key"}), 401
            return f(user_id=user_id, *args, **kwargs)
        return decorated_function

    def get_trace_context():
        carrier = {}
        inject(carrier)
        return carrier

    @app.errorhandler(HTTPException)
    def handle_exception(e):
        span = trace.get_current_span()
        span.record_exception(e)
        span.set_status(trace.Status(trace.StatusCode.ERROR, str(e)))
        response = e.get_response()
        error_id = str(uuid.uuid4())
        response.data = json.dumps({
            "code": e.code,
            "name": e.name,
            "error": e.description,
            "error_id": error_id
        })
        response.content_type = "application/json"
        return response

    @app.errorhandler(TimeoutError)
    def handle_timeout_error(e):
        span = trace.get_current_span()
        span.record_exception(e)
        span.set_status(trace.Status(trace.StatusCode.ERROR, str(e)))
        return jsonify({"error": "Request timed out"}), 504

    @app.errorhandler(ValueError)
    def handle_value_error(e):
        span = trace.get_current_span()
        span.record_exception(e)
        span.set_status(trace.Status(trace.StatusCode.ERROR, str(e)))
        return jsonify({"error": str(e)}), 400

    @app.errorhandler(Exception)
    def handle_unknown_exception(e):
        span = trace.get_current_span()
        span.record_exception(e)
        span.set_status(trace.Status(trace.StatusCode.ERROR, str(e)))
   
        error_id = str(uuid.uuid4())
        logger.exception(f"Unhandled exception occurred. Error ID: {error_id}")
        return jsonify({
            "error": "An unexpected error occurred",
            "error_id": error_id
        }), 500

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
        run_id = data.get("run_id")

        testbed = testbed_manager.get_or_create_testbed(instance_id, user_id=user_id, run_id=run_id)
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
        instance_id = data.get("instance_id")

        logger.debug(f"run_tests(testbed_id={testbed_id}, user_id={user_id}, instance_id={instance_id})")
        trace_context = get_trace_context()
        client = testbed_manager.create_client(testbed_id, instance_id=instance_id, user_id=user_id, trace_context=trace_context)
        client.wait_until_ready(timeout=600)

        result = client.run_tests(test_files, patch)
        return jsonify(result.model_dump()), 200

    @app.route("/testbeds/<testbed_id>/run-evaluation", methods=["POST"])
    @validate_api_key
    def run_evaluation(testbed_id: str, user_id: str):
        data = request.json
        patch = data.get("patch")
        instance_id = data.get("instance_id")
        logger.debug(f"run_evaluation(testbed_id={testbed_id}, user_id={user_id}, instance_id={instance_id})")

        trace_context = get_trace_context()
        client = testbed_manager.create_client(testbed_id, instance_id=instance_id, user_id=user_id, trace_context=trace_context)
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

    @app.route("/testbeds/<testbed_id>/status", methods=["GET"])
    @validate_api_key
    def get_testbed_status(testbed_id: str, user_id: str):
        try:
            trace_context = get_trace_context()
            status = testbed_manager.get_testbed_status(testbed_id, user_id, trace_context=trace_context)
            if not status:
                return jsonify({"error": "Testbed not found or unable to read status"}), 404

            return jsonify(status), 200
        except Exception as e:
            logger.exception(f"Error getting testbed status for {testbed_id}")
            return jsonify({"error": str(e)}), 500

    @app.route("/testbeds/<testbed_id>/exec", methods=["POST"])
    @validate_api_key
    def execute_command(testbed_id: str, user_id: str):
        data = request.json
        try:
            trace_context = get_trace_context()
            result, status_code = testbed_manager.proxy_exec(testbed_id, data, trace_context=trace_context)
            return jsonify(result), status_code
        except Exception as e:
            logger.exception(f"execute_command() Error during execution")
            return jsonify({"error": str(e)}), 500

    @app.route("/testbeds/<testbed_id>/exec", methods=["GET"])
    @validate_api_key
    def get_command_status(testbed_id: str, user_id: str):
        try:
            trace_context = get_trace_context()
            result, status_code = testbed_manager.proxy_exec_status(testbed_id, trace_context=trace_context)
            return jsonify(result), status_code
        except Exception as e:
            logger.exception(f"get_command_status() Error getting command status")
            return jsonify({"error": str(e)}), 500

    @app.route("/testbeds/<testbed_id>/file", methods=["GET"])
    @validate_api_key
    def get_file(testbed_id: str, user_id: str):
        file_path = request.args.get("file_path")
        if not file_path:
            return jsonify({"error": "Missing 'file_path' query parameter"}), 400

        try:
            trace_context = get_trace_context()
            result, status_code = testbed_manager.proxy_get_file(testbed_id, file_path, trace_context=trace_context)
            return jsonify(result), status_code
        except Exception as e:
            logger.exception(f"get_file() Error reading file")
            return jsonify({"error": str(e)}), 500

    @app.route("/testbeds/<testbed_id>/file", methods=["POST"])
    @validate_api_key
    def save_file(testbed_id: str, user_id: str):
        data = request.json
        if not data.get("file_path") or data.get("content") is None:
            return jsonify({"error": "Missing 'file_path' or 'content' in request body"}), 400

        try:
            trace_context = get_trace_context()
            result, status_code = testbed_manager.proxy_save_file(testbed_id, data, trace_context=trace_context)
            return jsonify(result), status_code
        except Exception as e:
            logger.exception(f"save_file() Error saving file")
            return jsonify({"error": str(e)}), 500

    @app.route("/testbeds/<testbed_id>/reset", methods=["POST"])
    @validate_api_key
    def reset_testbed(testbed_id: str, user_id: str):
        instance_id = None
        run_id = None

        try:
            data = request.json
            instance_id = data.get("instance_id")
            run_id = data.get("run_id")
        except Exception as e:
            logger.info(f"No JSON data provided in request body")
        
        try:
            restarted = testbed_manager.restart_if_running(testbed_id, user_id, run_id, instance_id)           
            return jsonify({
                "restarted": restarted
            }), 200
        except Exception as e:
            logger.exception(f"Error resetting testbed {testbed_id}")
            return jsonify({"error": str(e)}), 500

    return app


if __name__ == "__main__":
    app = create_app()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8000)), threaded=True)