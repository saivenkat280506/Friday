"""
process_monitor.py — Real-time Application Process Tracker
==========================================================
Monitors Windows active processes in a background thread and logs 
when applications are opened or closed.
"""

import subprocess
import csv
import threading
import time

_running_processes = set()
_monitor_thread = None
_monitoring = False

# Common system executables to ignore to reduce noise
IGNORE_LIST = {
    "svchost.exe", "conhost.exe", "cmd.exe", "powershell.exe", "tasklist.exe", 
    "wmiaprse.exe", "dllhost.exe", "runtimebroker.exe", "ctfmon.exe", 
    "searchhost.exe", "siihost.exe", "smartscreen.exe", "taskhostw.exe",
    "backgroundtaskhost.exe", "compkgsrv.exe", "shellexperiencehost.exe",
    "securityhealthservice.exe", "sppsvc.exe", "wscsvc.exe", "chkdsk.exe",
    "vcruntime140.dll", "python.exe", "py.exe", "npm.CMD", "node.exe"
}

def get_running_processes():
    """Fetches the set of currently running .exe processes on Windows."""
    try:
        # tasklist outputs CSV format: "Image Name","PID","Session Name"...
        res = subprocess.run("tasklist /fo csv /nh", shell=True, capture_output=True, text=True)
        if res.returncode != 0:
            return set()
        
        current_apps = set()
        reader = csv.reader(res.stdout.strip().splitlines())
        for row in reader:
            if row:
                exe_name = row[0].strip().lower()
                if exe_name.endswith(".exe"):
                    current_apps.add(exe_name)
        return current_apps
    except Exception as e:
        print(f"[ProcessMonitor] Error fetching processes: {e}")
        return set()

def _monitor_loop():
    global _running_processes, _monitoring
    print("[ProcessMonitor] Establishing process baseline...")
    _running_processes = get_running_processes()
    print(f"[ProcessMonitor] Baseline set with {len(_running_processes)} active processes.")
    
    while _monitoring:
        time.sleep(3.0)
        current = get_running_processes()
        if not current:
            continue
            
        opened = current - _running_processes
        closed = _running_processes - current
        
        for app in opened:
            if app not in IGNORE_LIST:
                app_clean = app.replace(".exe", "").capitalize()
                print(f"[ProcessMonitor] Detected App Opened: {app_clean} ({app})")
                
        for app in closed:
            if app not in IGNORE_LIST:
                app_clean = app.replace(".exe", "").capitalize()
                print(f"[ProcessMonitor] Detected App Closed: {app_clean} ({app})")
                
        _running_processes = current

def start_process_monitor():
    """Starts the process tracking background service."""
    global _monitor_thread, _monitoring
    if _monitoring:
        return
        
    _monitoring = True
    _monitor_thread = threading.Thread(target=_monitor_loop, name="FridayProcessMonitor")
    _monitor_thread.daemon = True
    _monitor_thread.start()
    print("[ProcessMonitor] Background monitoring service launched successfully.")

def stop_process_monitor():
    """Stops the process tracking background service."""
    global _monitoring
    _monitoring = False
    print("[ProcessMonitor] Background monitoring service stopped.")
