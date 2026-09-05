import asyncio
import os
import psutil
import logging

logger = logging.getLogger("background_monitor")

_last_alert_ts: dict[str, float] = {}
_ALERT_COOLDOWN_SEC = 600.0


async def run_background_monitor():
    """Background loop that checks system health every 5 minutes and pushes alerts."""
    print("[Background Monitor] Loop started.")
    await asyncio.sleep(10)  # Wait for startup to settle
    
    while True:
        try:
            # 1. Check CPU (non-blocking sample)
            cpu_percent = psutil.cpu_percent(interval=None)
            
            # 2. Check Memory (RAM)
            mem = psutil.virtual_memory()
            ram_percent = mem.percent
            
            # 3. Check Disk Space (cross-platform path)
            import platform
            if platform.system() == "Windows":
                system_drive = os.environ.get("SystemDrive", "C:") + os.sep
            else:
                system_drive = "/"
            disk = psutil.disk_usage(system_drive)
            disk_percent = disk.percent
            disk_free_gb = disk.free / (1024 ** 3)
            
            # 4. Push suggestions if threshold crossed
            from services.websocket_manager import ws_manager as manager
            
            import time
            now = time.time()

            def _should_alert(key: str) -> bool:
                last = _last_alert_ts.get(key, 0.0)
                if now - last < _ALERT_COOLDOWN_SEC:
                    return False
                _last_alert_ts[key] = now
                return True

            if cpu_percent > 85.0 and _should_alert("cpu"):
                alert = {
                    "type": "suggestion",
                    "text": f"Warning: System CPU usage is very high ({cpu_percent:.0f}%). Consider closing heavy applications."
                }
                print(f"[Background Monitor] CPU Alert: {cpu_percent}%")
                await manager.broadcast_json(alert)
                
            if ram_percent > 85.0 and _should_alert("ram"):
                alert = {
                    "type": "suggestion",
                    "text": f"Warning: System RAM usage is high ({ram_percent:.0f}%). FRIDAY will defer heavy local models until memory frees up."
                }
                print(f"[Background Monitor] RAM Alert: {ram_percent}%")
                await manager.broadcast_json(alert)
                
            if disk_free_gb < 10.0 and _should_alert("disk"):
                alert = {
                    "type": "suggestion",
                    "text": f"Warning: System disk space is low ({disk_free_gb:.1f} GB free). Consider cleaning up disk space."
                }
                print(f"[Background Monitor] Disk Alert: {disk_free_gb:.1f} GB free")
                await manager.broadcast_json(alert)
                
        except Exception as e:
            logger.error(f"Error in background monitor: {e}")
            
        await asyncio.sleep(300) # Sleep for 5 minutes
