# DEPRECATED: Use executor.whatsapp_handler.send_whatsapp_message instead.
# This module remains for reference; LegacySyncHandlers no longer routes here.

import subprocess
import time
import os
import sys
from pywinauto import Application
import pyautogui
from executor.window_manager import split_screen

try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

class WhatsAppController:
    def __init__(self):
        self.app = None
        self.main_window = None

    def focus_whatsapp_window(self):
        """Locates the WhatsApp window and forces active system focus."""
        import pygetwindow as gw
        whatsapp_wins = [w for w in gw.getAllWindows() if "whatsapp" in w.title.lower()]
        if whatsapp_wins:
            try:
                win = whatsapp_wins[0]
                if win.isMinimized:
                    win.restore()
                win.activate()
                time.sleep(0.5)  # Wait for active focus to settle
                return True
            except Exception as e:
                print(f"[WhatsAppController] pygetwindow focus failed: {e}")
        return False

    def open_whatsapp(self):
        user_home = os.path.expanduser("~")
        whatsapp_path = os.path.join(user_home, "AppData", "Local", "WhatsApp", "WhatsApp.exe")
        
        if os.path.exists(whatsapp_path):
            subprocess.Popen([whatsapp_path])
        else:
            # Try UWP URI protocol fallback
            subprocess.Popen(["cmd", "/c", "start", "whatsapp:"], shell=True)
            
        time.sleep(6)  # wait for launch
        
        # Connect to the window
        try:
            self.app = Application(backend="uia").connect(title_re=".*WhatsApp.*", timeout=5)
            self.main_window = self.app.window(title_re=".*WhatsApp.*")
            self.main_window.set_focus()
        except Exception as e:
            print(f"[WhatsAppController] Connection failed: {e}")
            # Fallback connection title
            try:
                self.app = Application(backend="uia").connect(title="Chats", timeout=5)
                self.main_window = self.app.window(title="Chats")
                self.main_window.set_focus()
            except Exception as e2:
                print(f"[WhatsAppController] Fallback connection failed: {e2}")
        
        # Force system active focus as well
        self.focus_whatsapp_window()
        return True

    def search_contact(self, contact_name):
        self.focus_whatsapp_window()
        print(f"[WhatsAppController] Searching contact '{contact_name}' using UIA DOM...")
        try:
            if self.main_window:
                search_box = self.main_window.child_window(title="Search or start a new chat", control_type="Edit", found_index=0)
                if search_box.exists():
                    search_box.click_input()
                    time.sleep(0.3)
                    search_box.type_keys("^a{BACKSPACE}" + contact_name + "{ENTER}", with_spaces=True)
                    time.sleep(1.5)
                    return
            
            # Keyboard fallback if main_window / search_box not found
            print("[WhatsAppController] DOM Search Box not found, falling back to hotkeys...")
            pyautogui.hotkey('ctrl', 'f')
            time.sleep(0.3)
            pyautogui.hotkey('ctrl', 'a')
            pyautogui.press('backspace')
            pyautogui.write(contact_name, interval=0.02)
            time.sleep(0.8)
            pyautogui.press('enter')
            time.sleep(0.5)
        except Exception as e:
            print(f"[WhatsAppController] Search failed: {e}")

    def send_message(self, message):
        self.focus_whatsapp_window()
        print(f"[WhatsAppController] Sending message via UIA DOM...")
        try:
            msg_box = None
            if self.main_window:
                # Strategy 1: exact CSS class name
                try:
                    box = self.main_window.child_window(class_name="x1hx0egp x6ikm8r x1odjw0f x1k6rcq7 x6prxxf", control_type="Edit", found_index=0)
                    if box.exists():
                        msg_box = box
                except Exception:
                    pass

                # Strategy 2: regex class name
                if not msg_box:
                    try:
                        box = self.main_window.child_window(class_name_re=".*x1hx0egp.*", control_type="Edit", found_index=0)
                        if box.exists():
                            msg_box = box
                    except Exception:
                        pass

                # Strategy 3: control type "Edit" generally (usually message box is the last Edit or we can search for it)
                if not msg_box:
                    try:
                        edits = self.main_window.descendants(control_type="Edit")
                        for edit in reversed(edits):
                            rect = edit.rectangle()
                            if rect.top > 500: # Message box is near the bottom
                                msg_box = edit
                                break
                        if not msg_box and edits:
                            msg_box = edits[-1]
                    except Exception:
                        pass

            if msg_box:
                is_spec = hasattr(msg_box, "exists")
                if not is_spec or msg_box.exists():
                    print("[WhatsAppController] Message box found via DOM.")
                    msg_box.click_input()
                    time.sleep(0.5)
                    msg_box.type_keys(message, with_spaces=True)
                    time.sleep(0.3)
                    msg_box.type_keys("{ENTER}")
                    return "Message sent"
            
            # Keyboard-first fallback: click message area -> type -> Enter (most reliable)
            print("[WhatsAppController] DOM Message Box not found, using keyboard-first fallback...")
            if self.main_window:
                try:
                    rect = self.main_window.rectangle()
                    # Click bottom middle of the window where message box is
                    click_x = rect.left + (rect.right - rect.left) // 2
                    click_y = rect.bottom - 60
                    pyautogui.click(click_x, click_y)
                    time.sleep(0.5)
                except Exception as e:
                    print(f"[WhatsAppController] Clicking bottom area failed: {e}")
            
            # Type and enter
            pyautogui.write(message, interval=0.02)
            time.sleep(0.3)
            pyautogui.press('enter')
            return "Message sent"
        except Exception as e:
            print(f"[WhatsAppController] Send failed: {e}")
            return "Message sent via fallback"

    def close_whatsapp(self):
        try:
            os.system("taskkill /f /im WhatsApp.exe")
            time.sleep(1.0)
        except Exception as e:
            print(f"[WhatsAppController] taskkill failed: {e}")

def send_whatsapp_message(contact: str, message: str) -> tuple:
    print(f"[WhatsAppController] Automating message to {contact}...")
    wc = WhatsAppController()
    try:
        # Close any stale background instance first to ensure fresh state
        wc.close_whatsapp()
        
        wc.open_whatsapp()
        wc.search_contact(contact)
        wc.send_message(message)
        
        # Arrange windows side-by-side: FRIDAY left, WhatsApp right
        try:
            time.sleep(1.0)
            split_screen("FRIDAY", "WhatsApp", left_ratio=0.4)
        except Exception as e:
            print(f"[WhatsAppController] Split screen arrangement failed: {e}")
            
        return True, f"✅ Message sent to {contact} on WhatsApp (split view enabled)"
    except Exception as e:
        print(f"[WhatsAppController] Execution failed: {e}")
        return False, f"❌ Failed to send WhatsApp message to {contact}: {e}"
