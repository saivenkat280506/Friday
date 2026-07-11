"""
task_manager.py — FRIDAY Task Management System
===============================================
Handles tracking, parallel execution, and priority-based scheduling of tasks.
"""

import asyncio
import uuid
from datetime import datetime
from typing import Dict, Any, Optional, List, Tuple

class TaskManager:
    def __init__(self):
        # PRIORITY_MAP: Lower value = Higher priority (for PriorityQueue)
        self.PRIORITY_MAP = {"high": 0, "medium": 1, "low": 2}
        
        # active_tasks: {task_id: (asyncio.Task, metadata)}
        self.active_tasks: Dict[str, tuple] = {}
        # type_mapping: {task_type: set(task_ids)}
        self.type_mapping: Dict[str, set] = {}
        # task_history: {task_id: {"status": str, "result": Any, "start_time": str}}
        self.task_history: Dict[str, Dict[str, Any]] = {}
        
        # Priority Queue for scheduling
        self.queue = asyncio.PriorityQueue()
        self._scheduler_started = False

    async def _scheduler_loop(self):
        """
        Background loop that processes the priority queue.
        """
        print("[TaskManager] Scheduler loop started.")
        while True:
            try:
                # Get the highest priority task from the queue
                # Item format: (priority_val, task_id, coro, name, task_type, metadata)
                priority_val, task_id, coro, name, task_type, metadata = await self.queue.get()
                
                # Artificial delay for low priority tasks to simulate "background" processing
                if priority_val == 2: # Low
                    await asyncio.sleep(0.5)
                
                # Create the actual asyncio Task
                task = asyncio.create_task(coro, name=name)
                
                # Store in active tasks
                self.active_tasks[task_id] = (task, {
                    "name": name,
                    "type": task_type,
                    "metadata": metadata or {},
                    "start_time": datetime.now().isoformat(),
                    "priority": priority_val
                })
                
                # Update type mapping
                if task_type not in self.type_mapping:
                    self.type_mapping[task_type] = set()
                self.type_mapping[task_type].add(task_id)
                
                # Attach completion callback
                task.add_done_callback(lambda t: self._handle_completion(task_id, t))
                
                # Mark queue item as processed
                self.queue.task_done()
                
            except Exception as e:
                print(f"[TaskManager] Scheduler Error: {e}")
                await asyncio.sleep(1)

    async def start_task(self, coro, name: str = "Unnamed Task", task_type: str = "general", priority: str = "medium", metadata: Dict = None):
        """
        Schedules a coroutine for execution based on priority.
        """
        # Start scheduler on first task request
        if not self._scheduler_started:
            asyncio.create_task(self._scheduler_loop())
            self._scheduler_started = True

        task_id = str(uuid.uuid4())[:8]
        priority_val = self.PRIORITY_MAP.get(priority, 1)
        
        # Put into PriorityQueue
        # Format: (priority_value, unique_id, coroutine, name, type, metadata)
        # unique_id is added to prevent PriorityQueue from comparing coroutines if priorities are equal
        await self.queue.put((priority_val, task_id, coro, name, task_type, metadata))
        
        return task_id

    def _handle_completion(self, task_id: str, task: asyncio.Task):
        """Callback triggered when a task finishes."""
        if task_id not in self.active_tasks:
            return

        _, meta = self.active_tasks[task_id]
        try:
            result = task.result()
            status = "completed"
        except asyncio.CancelledError:
            result = "Cancelled"
            status = "cancelled"
        except Exception as e:
            result = str(e)
            status = "failed"

        # Move to history
        self.task_history[task_id] = {
            "name": meta["name"],
            "type": meta["type"],
            "status": status,
            "result": result,
            "end_time": datetime.now().isoformat(),
            "priority": meta.get("priority")
        }
        
        # Cleanup: Remove from active and type mapping
        task_type = meta["type"]
        if task_type in self.type_mapping:
            self.type_mapping[task_type].discard(task_id)
        
        del self.active_tasks[task_id]
        print(f"[TaskManager] Task {task_id} ({meta['name']}) finished with status: {status}")

    def get_task_status(self, task_id: str) -> Optional[Dict]:
        """Returns the status of a task from active or history."""
        if task_id in self.active_tasks:
            return {"status": "running", "name": self.active_tasks[task_id][1]["name"]}
        return self.task_history.get(task_id)

    def cancel_task(self, task_id: str) -> bool:
        """Cancels a running task."""
        if task_id in self.active_tasks:
            task, _ = self.active_tasks[task_id]
            task.cancel()
            return True
        return False

    def get_tasks_by_type(self, task_type: str) -> List[str]:
        """Returns a list of active task IDs for a given type."""
        return list(self.type_mapping.get(task_type, []))

    def cancel_task_by_type(self, task_type: str) -> int:
        """Cancels all active tasks of a specific type."""
        task_ids = self.get_tasks_by_type(task_type)
        count = 0
        for tid in task_ids:
            if self.cancel_task(tid):
                count += 1
        return count

    def get_all_running(self):
        """Returns list of currently active tasks."""
        return {tid: meta["name"] for tid, (_, meta) in self.active_tasks.items()}

# Global instance
task_manager = TaskManager()

