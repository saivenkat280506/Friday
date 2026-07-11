"""
agent_loop.py — Background Monitoring Agent
============================================
Continuously checks for failed tasks, handles retries, and manages scheduled items.
"""

import asyncio
import time
from executor.task_manager import task_manager
from brain.response_builder import get_builder
from tts.pocket_tts import speak

class AgentLoop:
    def __init__(self):
        self.is_running = False
        self.retry_queue = [] # List of (task_id, coro, attempt_count, metadata)

    async def run(self):
        """The main background loop."""
        self.is_running = True
        print("[AgentLoop] Background monitor started.")
        
        while self.is_running:
            try:
                await self._check_and_retry_tasks()
                await asyncio.sleep(2) # Loop interval
            except Exception as e:
                print(f"[AgentLoop Error] {e}")
                await asyncio.sleep(5)

    async def _check_and_retry_tasks(self):
        """Scans history for failures and attempts smart retries."""
        # We look for tasks in history marked as 'failed' that haven't been retried
        # In a real system, we'd track retry counts per task_id
        # For now, we handle tasks added to our specific retry_queue
        
        if not self.retry_queue:
            return

        print(f"[AgentLoop] Processing {len(self.retry_queue)} pending retries...")
        
        # Process a copy to allow modification of the original queue
        current_queue = list(self.retry_queue)
        self.retry_queue.clear()

        for task_info in current_queue:
            task_id, coro, attempt, meta = task_info
            
            if attempt < 2:
                print(f"[AgentLoop] Retrying task {task_id} (Attempt {attempt+1}/2)...")
                # Wait a bit before retry
                await asyncio.sleep(1 * (attempt + 1))
                
                # Re-submit to task manager
                await task_manager.start_task(coro, name=meta.get("name", "Retry Task"), metadata=meta)
            else:
                # Final failure
                print(f"[AgentLoop] Task {task_id} failed after max retries.")
                # Notify user via TTS - Friday tone
                speak(get_builder().error())

    def add_to_retry_queue(self, coro, metadata):
        """Adds a failed task to the retry queue."""
        # we store the coroutine factory or the coro itself
        # Note: coroutines cannot be reused, so we expect a callable that returns a coro
        self.retry_queue.append(( "retry_" + str(int(time.time())), coro, 0, metadata))

# Global instance
agent_loop = AgentLoop()
