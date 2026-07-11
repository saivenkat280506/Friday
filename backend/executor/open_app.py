"""
open_app.py — Application Launcher
==================================
Handles launching system applications using protocols, shell commands, and fallbacks.
"""

import subprocess
import webbrowser
import os
import shutil
import json

def get_app_path(app_name: str) -> str:
    """Helper to resolve the path of an application using registry, start menu, or PATH."""
    name_lower = app_name.lower().strip()
    
    # ── Check memory for learned app path first ──
    try:
        from brain.memory_store import get_memory_store
        store = get_memory_store()
        if store and store.is_ready:
            pref_key = f"app_path_{name_lower}"
            cached_path_data = store.get_preference(pref_key)
            if cached_path_data and "metadata" in cached_path_data:
                metadata = cached_path_data["metadata"]
                if metadata and "value" in metadata:
                    cached_path = metadata["value"]
                    if os.path.exists(cached_path) or cached_path.endswith(":"):
                        print(f"[Launcher] Preference hit for '{name_lower}': {cached_path}")
                        return cached_path
    except Exception as e:
        print(f"[Launcher] Error reading preference memory for '{name_lower}': {e}")
        
    # 1. Alias check
    aliases = {
        "calculator": "calc",
        "paint": "mspaint",
        "cmd": "cmd.exe",
        "command prompt": "cmd.exe",
        "terminal": "wt.exe",
        "powershell": "powershell.exe",
        "task manager": "taskmgr.exe",
        "taskmanager": "taskmgr.exe",
        "settings": "ms-settings:",
        "control panel": "control",
        "file explorer": "explorer.exe",
        "explorer": "explorer.exe",
        "notepad": "notepad.exe",
        "notepads": "notepad.exe",
        "spotify": "spotify:",
        "discord": "discord:",
        "telegram": "telegram:",
    }
    if name_lower in aliases:
        name_lower = aliases[name_lower]
        
    if name_lower.endswith(":"):
        return name_lower
        
    # 2. Registry search
    registry = {}
    try:
        current_dir = os.path.dirname(os.path.abspath(__file__))
        registry_path = os.path.join(current_dir, "apps_registry.json")
        if os.path.exists(registry_path):
            with open(registry_path, "r", encoding="utf-8") as f:
                registry = json.load(f)
    except Exception:
        pass
        
    if name_lower in registry:
        return registry[name_lower]
        
    for key, val in registry.items():
        if name_lower in key or key in name_lower:
            return val
            
    # 3. Start Menu crawling
    user_programs = os.path.join(os.environ.get("APPDATA", ""), "Microsoft", "Windows", "Start Menu", "Programs")
    common_programs = os.path.join(os.environ.get("ALLUSERSPROFILE", "C:\\ProgramData"), "Microsoft", "Windows", "Start Menu", "Programs")
    search_dirs = [user_programs, common_programs]
    for search_dir in search_dirs:
        if os.path.exists(search_dir):
            for root, dirs, files in os.walk(search_dir):
                for file in files:
                    if file.lower().endswith(".lnk"):
                        link_name = os.path.splitext(file)[0].lower()
                        if name_lower in link_name or link_name in name_lower:
                            return os.path.join(root, file)
                            
    # 4. Path check using shutil.which
    exe_path = shutil.which(name_lower)
    if exe_path:
        return exe_path
        
    return name_lower

def save_app_path_preference(app_name: str, path: str):
    """Save resolved app path to preference memory."""
    try:
        from brain.memory_store import get_memory_store
        store = get_memory_store()
        if store and store.is_ready:
            pref_key = f"app_path_{app_name.lower().strip()}"
            store.store_preference(pref_key, path, confidence=1.0)
            print(f"[Launcher] Learned preference: {pref_key} -> {path}")
    except Exception as e:
        print(f"[Launcher] Failed to save app path preference: {e}")

def open_app(app_name: str):
    """
    Opens a specified application based on Windows-specific logic.
    
    Args:
        app_name (str): Name of the application to launch.
        
    Returns:
        tuple: (success: bool, message: str)
    """
    name_lower = app_name.lower().strip()
    
    # 1. WhatsApp: Use local executable check and protocol fallback
    if "whatsapp" in name_lower:
        try:
            from executor.automation import open_whatsapp
            res = open_whatsapp()
            if isinstance(res, tuple) and res[0]:
                save_app_path_preference(app_name, "whatsapp:")
            return res
        except ImportError:
            try:
                from automation import open_whatsapp
                res = open_whatsapp()
                if isinstance(res, tuple) and res[0]:
                    save_app_path_preference(app_name, "whatsapp:")
                return res
            except ImportError:
                user_home = os.path.expanduser("~")
                whatsapp_path = os.path.join(user_home, "AppData", "Local", "WhatsApp", "WhatsApp.exe")
                if os.path.exists(whatsapp_path):
                    try:
                        subprocess.Popen([whatsapp_path], shell=False)
                        save_app_path_preference(app_name, whatsapp_path)
                        return True, "Successfully opened WhatsApp Desktop."
                    except Exception as e:
                        pass
                try:
                    subprocess.run("start whatsapp:", shell=True, check=True)
                    save_app_path_preference(app_name, "whatsapp:")
                    return True, "Successfully opened WhatsApp using protocol."
                except Exception as e:
                    return False, f"failed to open {app_name}: {str(e)}"

    # 2. Browser (Chrome or Arc with Fallback)
    if "chrome" in name_lower or "google chrome" in name_lower:
        chrome_paths = [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            os.path.join(os.environ.get("LOCALAPPDATA", ""), r"Google\Chrome\Application\chrome.exe")
        ]
        launched = False
        for path in chrome_paths:
            if os.path.exists(path):
                try:
                    subprocess.Popen([path, "--profile-directory=Default"], shell=False)
                    launched = True
                    save_app_path_preference(app_name, path)
                    return True, "Successfully opened Chrome (bypassing profile picker)."
                except Exception:
                    pass
        if not launched:
            try:
                webbrowser.open("about:blank")
                return True, "Opened default browser as fallback."
            except Exception as e:
                return False, f"failed to open Chrome: {str(e)}"

    if name_lower == "browser" or name_lower == "arc":
        try:
            from executor.automation import open_browser
            res = open_browser()
            if isinstance(res, tuple) and res[0]:
                save_app_path_preference(app_name, "browser")
            return res
        except ImportError:
            from automation import open_browser
            res = open_browser()
            if isinstance(res, tuple) and res[0]:
                save_app_path_preference(app_name, "browser")
            return res

    # 3. Resolve app path
    path = get_app_path(app_name)
    if path.endswith(":"):
        try:
            os.startfile(path)
            save_app_path_preference(app_name, path)
            return True, f"Successfully opened {app_name}."
        except Exception as e:
            # Fallback to web interface
            web_fallbacks = {
                "spotify": "https://open.spotify.com",
                "discord": "https://discord.com/app",
                "telegram": "https://web.telegram.org",
                "whatsapp": "https://web.whatsapp.com"
            }
            matched_app = None
            for key in web_fallbacks:
                if key in app_name.lower() or key in path.lower():
                    matched_app = key
                    break
            if matched_app:
                try:
                    from executor.automation import open_url_in_chrome
                except ImportError:
                    from automation import open_url_in_chrome
                open_url_in_chrome(web_fallbacks[matched_app])
                return True, f"App {app_name} launch failed. Opened web version in Chrome as fallback."
            return False, f"failed to open {app_name}: {str(e)}"
            
    if os.path.exists(path):
        try:
            os.startfile(path)
            save_app_path_preference(app_name, path)
            return True, f"Successfully opened {app_name}."
        except Exception as e:
            try:
                subprocess.Popen(f'start "" "{path}"', shell=True)
                save_app_path_preference(app_name, path)
                return True, f"Successfully opened {app_name}."
            except Exception as ex:
                pass
                
    # Fallback to system path execution
    try:
        exe_path = shutil.which(path)
        if exe_path:
            subprocess.Popen(exe_path, shell=False)
            save_app_path_preference(app_name, exe_path)
            return True, f"Successfully opened {app_name}."
    except Exception:
        pass
        
    # If everything fails, try web fallback as a last resort before giving up
    web_fallbacks = {
        "spotify": "https://open.spotify.com",
        "discord": "https://discord.com/app",
        "telegram": "https://web.telegram.org",
        "whatsapp": "https://web.whatsapp.com"
    }
    matched_app = None
    for key in web_fallbacks:
        if key in app_name.lower() or key in path.lower():
            matched_app = key
            break
    if matched_app:
        try:
            from executor.automation import open_url_in_chrome
        except ImportError:
            from automation import open_url_in_chrome
        open_url_in_chrome(web_fallbacks[matched_app])
        return True, f"App {app_name} not found. Opened web version in Chrome as fallback."
        
    return False, f"failed to open {app_name}. Please make sure it is installed."

if __name__ == "__main__":
    # Test cases
    test_apps = ["whatsapp", "browser", "notepad", "calc"]
    
    for app in test_apps:
        print(f"Testing {app}...")
        success, msg = open_app(app)
        print(f"Result: {success} {msg}\n")
