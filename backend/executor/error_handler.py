"""
error_handler.py — Retry and Error Logging System
=================================================
Provides a robust mechanism to retry failing tasks and log errors for debugging.
"""

import time
import logging
import asyncio
from datetime import datetime
from functools import wraps
from typing import Callable, Any, TypeVar, cast

T = TypeVar('T')

# Log file configuration
LOG_FILE = "logs.txt"

def log_error(task_name: str, error: Exception):
    """
    Logs a task failure to a text file with a timestamp.
    """
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    error_msg = str(error)
    log_entry = f"[{timestamp}] TASK: {task_name} | ERROR: {error_msg}\n"
    
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(log_entry)
    except Exception as e:
        print(f"[Critical] Could not write to log file: {e}")

def retry_task(max_retries: int = 3, delay: float = 1.0):
    """
    A decorator that retries a synchronous function up to max_retries times.
    """
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @wraps(func)
        def wrapper(*args, **kwargs):
            last_exception = None
            task_name = func.__name__
            
            for attempt in range(1, max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    last_exception = e
                    print(f"[Retry] {task_name} attempt {attempt}/{max_retries} failed: {e}")
                    if attempt < max_retries:
                        time.sleep(delay)
            
            # All attempts failed
            log_error(task_name, last_exception)
            return "Task failed" # Or raise the exception depending on preference
            
        return wrapper
    return decorator

def retry_async_task(max_retries: int = 3, delay: float = 1.0):
    """
    A decorator that retries an asynchronous function up to max_retries times.
    """
    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        @wraps(func)
        async def wrapper(*args, **kwargs):
            last_exception = None
            task_name = func.__name__
            
            for attempt in range(1, max_retries + 1):
                try:
                    return await func(*args, **kwargs)
                except Exception as e:
                    last_exception = e
                    print(f"[Retry] {task_name} attempt {attempt}/{max_retries} failed: {e}")
                    if attempt < max_retries:
                        await asyncio.sleep(delay)
            
            # All attempts failed
            log_error(task_name, last_exception)
            return "Task failed"
            
        return wrapper
    return decorator

if __name__ == "__main__":
    # --- Test Suite ---
    
    # 1. Test Sync Retry
    @retry_task(max_retries=3)
    def fail_sync():
        print("Trying sync task...")
        raise RuntimeError("Sync failure!")
    
    # 2. Test Async Retry
    @retry_async_task(max_retries=3)
    async def fail_async():
        print("Trying async task...")
        raise RuntimeError("Async failure!")

    print("Starting Sync Test...")
    print(f"Result: {fail_sync()}")
    
    print("\nStarting Async Test...")
    result = asyncio.run(fail_async())
    print(f"Result: {result}")
    
    print(f"\nCheck {LOG_FILE} for logs.")
