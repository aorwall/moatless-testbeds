import sys
import time
import logging

logger = logging.getLogger(__name__)


def wait_for_testbed_ready(manager, testbed_id, timeout=300, interval=1):
    start_time = time.time()
    last_status = {}

    def print_status(status, elapsed_time):
        output = [
            f"Testbed ID: {testbed_id}",
            f"Pod Phase: {status['pod_phase']}",
            f"External IP: {status['external_ip'] or 'Not assigned yet'}",
            "Testbed Container:",
            f"  Ready: {status['testbed_ready']}",
            f"  State: {status['testbed_state']}",
            "Sidecar Container:",
            f"  Ready: {status['sidecar_ready']}",
            f"  State: {status['sidecar_state']}",
            f"\nWaiting for testbed to be ready... (Elapsed time: {int(elapsed_time)}s)",
        ]
        if status["testbed_reason"]:
            output.insert(5, f"  Reason: {status['testbed_reason']}")
        if status["sidecar_reason"]:
            output.insert(-1, f"  Reason: {status['sidecar_reason']}")

        if "IPython" in sys.modules:
            from IPython.display import clear_output

            clear_output(wait=True)
            print("\n".join(output))
        else:
            logger.info("\n".join(output))

    not_found_count = 0
    while time.time() - start_time < timeout:
        testbed = manager.get_testbed(testbed_id)
        if not testbed:
            not_found_count += 1
            if not_found_count > 10:
                raise TimeoutError(
                    f"Testbed {testbed_id} not found after {timeout} seconds"
                )
            current_status = {
                "pod_phase": "Not found",
                "external_ip": "Not found",
                "testbed_ready": False,
                "testbed_state": "Not found",
                "testbed_reason": "Not found",
                "sidecar_ready": False,
                "sidecar_state": "Not found",
                "sidecar_reason": "Not found",
            }
        else:
            current_status = {
                "pod_phase": testbed.status.pod_phase,
                "external_ip": testbed.external_ip,
                "testbed_ready": testbed.status.testbed.ready,
                "testbed_state": testbed.status.testbed.state,
                "testbed_reason": testbed.status.testbed.reason,
                "sidecar_ready": testbed.status.sidecar.ready,
                "sidecar_state": testbed.status.sidecar.state,
                "sidecar_reason": testbed.status.sidecar.reason,
            }

        if current_status != last_status:
            print_status(current_status, time.time() - start_time)
            last_status = current_status

        if (
            current_status["pod_phase"] == "Running"
            and current_status["external_ip"]
            and current_status["testbed_ready"]
            and current_status["sidecar_ready"]
        ):
            finish_text = f"Testbed {testbed_id} is ready and can be reached on http://{current_status['external_ip']}:8000!"
            if "IPython" in sys.modules:
                from IPython.display import clear_output
                print(finish_text)
            else:
                logger.info(finish_text)
            return testbed

        time.sleep(interval)

    if "IPython" in sys.modules:
        print(
            f"\nTimeout reached. Testbed {testbed_id} is not fully ready after {timeout} seconds."
        )
    else:
        logger.error(
            f"\nTimeout reached. Testbed {testbed_id} is not fully ready after {timeout} seconds."
        )
    return None
