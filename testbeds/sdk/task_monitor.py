import asyncio
import logging
import sys
import threading
import time
import traceback
from typing import Dict

logger = logging.getLogger(__name__)

class AsyncTaskMonitor:
    @staticmethod
    def log_blocking_debug_info():
        """Log detailed debug information about the current async event loop state."""
        logger.error("=== Debug Info: Potential blocking detected in async event loop ===")
        logger.error("Current thread: %s", threading.current_thread())
        logger.error("Active threads: %s", threading.enumerate())
        
        tasks = list(asyncio.all_tasks())
        logger.error("Total active asyncio tasks: %d", len(tasks))
        
        for task in tasks:
            logger.error("Task: %s, Coroutine: %s", task, task.get_coro())
            try:
                stack = task.get_stack()
                if stack:
                    # Format each frame in the stack
                    formatted_stack = "\n".join("".join(traceback.format_stack(frame)) for frame in stack)
                    logger.error("Stack trace for task %s:\n%s", task, formatted_stack)
                else:
                    logger.error("No stack trace available for task %s", task)
                    
                # Additional task info
                logger.error("Task state: done=%s, cancelled=%s", task.done(), task.cancelled())
                if task.done() and not task.cancelled():
                    exc = task.exception()
                    if exc:
                        logger.error("Task exception: %s", exc)
                        
            except Exception as e:
                logger.error("Error getting task info: %s", e)
                
        # Log event loop info
        try:
            loop = asyncio.get_running_loop()
            logger.error("Event loop running: %s, closed: %s", loop.is_running(), loop.is_closed())
        except Exception as e:
            logger.error("Error getting loop info: %s", e)
            
        logger.error("=== End of Debug Info ===")

    @staticmethod
    def add_task_done_callback(task: asyncio.Task):
        """Add a callback to monitor task completion."""
        def _done_callback(task: asyncio.Task):
            try:
                if task.cancelled():
                    AsyncTaskMonitor.log_blocking_debug_info()
            except Exception as e:
                logger.error("Error in task monitor callback: %s", e)

        task.add_done_callback(_done_callback)

# Helper function to get current task info when needed
def get_current_task_info() -> Dict:
    """Get information about the current task and its context."""
    current = asyncio.current_task()
    if not current:
        return {}
        
    return {
        'task_id': id(current),
        'name': current.get_name(),
        'done': current.done(),
        'cancelled': current.cancelled(),
        'stack': traceback.extract_stack(),
        'loop': str(current._loop),
        'active_tasks': len(asyncio.all_tasks())
    } 