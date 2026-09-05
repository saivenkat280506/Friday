try:
    import pygetwindow as gw
except ImportError:
    gw = None
import pyautogui
import time

def split_screen(left_window_title: str, right_window_title: str, left_ratio: float = 0.5):
    """Arrange two windows side by side on the primary monitor."""
    if gw is None:
        return "Window manager is not available on this platform."
    print(f"[WindowManager] Attempting to arrange: Left='{left_window_title}', Right='{right_window_title}'")
    
    # Wait a bit for WhatsApp Desktop state to stabilize
    time.sleep(1.0)
    
    def clean_title(t: str) -> str:
        return t.lower().replace(".", "").replace(" ", "").strip()
    
    left_wins = [w for w in gw.getAllWindows() if clean_title(left_window_title) in clean_title(w.title)]
    right_wins = [w for w in gw.getAllWindows() if clean_title(right_window_title) in clean_title(w.title)]

    if not left_wins:
        print(f"[WindowManager] Could not find left window matching '{left_window_title}'")
    if not right_wins:
        print(f"[WindowManager] Could not find right window matching '{right_window_title}'")

    if not left_wins or not right_wins:
        # Log all open window titles to help debugging
        print("[WindowManager] Open window titles: " + str([w.title for w in gw.getAllWindows() if w.title]))
        return f"Could not find windows: {left_window_title} or {right_window_title}"

    left_win = left_wins[0]
    right_win = right_wins[0]

    # Ensure windows are restored (not minimized/maximized) so they can be resized/moved
    try:
        if left_win.isMinimized:
            left_win.restore()
        if right_win.isMinimized:
            right_win.restore()
    except Exception as e:
        print(f"[WindowManager] Window restore error: {e}")

    # Use robust pyautogui screen size detection
    screen_width, screen_height = pyautogui.size()
    # Account for Windows taskbar (roughly 40px at the bottom)
    work_height = screen_height - 40

    left_width = int(screen_width * left_ratio)
    right_width = screen_width - left_width

    try:
        # Move & Resize Left Window
        left_win.moveTo(0, 0)
        left_win.resizeTo(left_width, work_height)
        
        # Move & Resize Right Window
        right_win.moveTo(left_width, 0)
        right_win.resizeTo(right_width, work_height)

        # Focus both
        left_win.activate()
        right_win.activate()
        
        print("[WindowManager] Split screen arrangement completed successfully.")
        return f"Windows arranged: {left_window_title} left, {right_window_title} right"
    except Exception as e:
        print(f"[WindowManager] Resizing/moving failed: {e}")
        return f"Error arranging windows: {e}"

def bring_friday_to_front():
    """Bring the FRIDAY Electron window to front."""
    import platform
    if platform.system() == "Windows":
        import ctypes
        user32 = ctypes.windll.user32
        
        def enum_callback(hwnd, _):
            if user32.IsWindowVisible(hwnd):
                length = user32.GetWindowTextLengthW(hwnd)
                buf = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buf, length + 1)
                title = buf.value.lower().strip()
                if title == "f.r.i.d.a.y.":
                    user32.ShowWindow(hwnd, 9)  # SW_RESTORE
                    user32.SetForegroundWindow(hwnd)
                    return False  # Stop enumerating
            return True
            
        WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int))
        user32.EnumWindows(WNDENUMPROC(enum_callback), 0)
    elif platform.system() == "Darwin":
        try:
            import subprocess
            subprocess.run(
                ["osascript", "-e", 'tell application "Electron" to activate'],
                capture_output=True,
                timeout=1.0,
            )
        except Exception:
            pass
    else:
        if gw is None or not hasattr(gw, "getAllWindows"):
            return
        try:
            friday_wins = [w for w in gw.getAllWindows() if w.title and w.title.lower().strip() == "f.r.i.d.a.y."]
            if friday_wins:
                if friday_wins[0].isMinimized:
                    friday_wins[0].restore()
                friday_wins[0].activate()
        except Exception as e:
            print(f"[WindowManager] Bring to front failed: {e}")
