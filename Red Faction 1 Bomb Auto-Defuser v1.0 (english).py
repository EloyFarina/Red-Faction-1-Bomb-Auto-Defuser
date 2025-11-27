import tkinter as tk
from tkinter import messagebox, ttk
import threading
import time
from pynput import keyboard
import pygetwindow as gw
import ctypes
import pyautogui

# =================================================================================
# --- DIRECTINPUT INJECTION ENGINE (LOW-LEVEL) ---
# =================================================================================

PUL = ctypes.POINTER(ctypes.c_ulong)
class KeyBdInput(ctypes.Structure): _fields_ = [("wVk", ctypes.c_ushort), ("wScan", ctypes.c_ushort), ("dwFlags", ctypes.c_ulong), ("time", ctypes.c_ulong), ("dwExtraInfo", PUL)]
class HardwareInput(ctypes.Structure): _fields_ = [("uMsg", ctypes.c_ulong), ("wParamL", ctypes.c_short), ("wParamH", ctypes.c_ushort)]
class MouseInput(ctypes.Structure): _fields_ = [("dx", ctypes.c_long), ("dy", ctypes.c_long), ("mouseData", ctypes.c_ulong), ("dwFlags", ctypes.c_ulong), ("time", ctypes.c_ulong), ("dwExtraInfo", PUL)]
class Input_I(ctypes.Union): _fields_ = [("ki", KeyBdInput), ("mi", MouseInput), ("hi", HardwareInput)]
class Input(ctypes.Structure): _fields_ = [("type", ctypes.c_ulong), ("ii", Input_I)]

# Scan Codes for the direction keys
SCAN_CODES = {'up': 0x48, 'down': 0x50, 'left': 0x4B, 'right': 0x4D}
EXTENDED_KEYS = [0x48, 0x50, 0x4B, 0x4D]

def PressKey(hexKeyCode):
    extra = ctypes.c_ulong(0); ii_ = Input_I(); flags = 0x0008
    if hexKeyCode in EXTENDED_KEYS: flags |= 0x0001
    ii_.ki = KeyBdInput(0, hexKeyCode, flags, 0, ctypes.pointer(extra))
    x = Input(ctypes.c_ulong(1), ii_)
    ctypes.windll.user32.SendInput(1, ctypes.pointer(x), ctypes.sizeof(x))

def ReleaseKey(hexKeyCode):
    extra = ctypes.c_ulong(0); ii_ = Input_I(); flags = 0x0008 | 0x0002
    if hexKeyCode in EXTENDED_KEYS: flags |= 0x0001
    ii_.ki = KeyBdInput(0, hexKeyCode, flags, 0, ctypes.pointer(extra))
    x = Input(ctypes.c_ulong(1), ii_)
    ctypes.windll.user32.SendInput(1, ctypes.pointer(x), ctypes.sizeof(x))

def tap_key(key_name):
    """Quickly presses a mapped key."""
    scan_code = SCAN_CODES.get(key_bindings.get(key_name))
    if scan_code:
        PressKey(scan_code)
        time.sleep(0.1) # Quick press
        ReleaseKey(scan_code)

# =================================================================================
# --- GLOBAL CONFIGURATION ---
# =================================================================================

led_boundary = None  # Single coordinate of the LED (Red/Green for step)
stage1_success_boundary = None # Slot 4 (Used by the Bot)
# test_boundary = None # Removed
key_bindings = { 'up': None, 'down': None, 'left': None, 'right': None }
current_direction_to_set = None
listener = None
running = False
GAME_WINDOW_TITLE = None

# =================================================================================
# --- VISION: LED STATUS AND SLOT 4 DETECTION (Pixel Scanning) ---
# =================================================================================

def get_led_state(bbox):
    """
    Analyzes the main LED area (Red=Step Success, Green=Failure/Reset/LevelStart).
    Returns: 'GREEN' (Failure/Reset/LevelStart), 'RED' (Active/Success) or 'UNKNOWN'.
    """
    if not bbox: return 'UNKNOWN'
    try:
        width = bbox[2] - bbox[0]
        height = bbox[3] - bbox[1]
        if width <= 0 or height <= 0: return 'UNKNOWN'

        # Capture the region
        img = pyautogui.screenshot(region=(bbox[0], bbox[1], width, height))

        # Analyze the center
        cx, cy = width // 2, height // 2
        r, g, b = img.getpixel((cx, cy))

        if g > (r + 40): # Dominant green
            return 'GREEN'
        if r > (g + 20): # Dominant red
            return 'RED'

        return 'UNKNOWN'
    except Exception as e:
        print(f"Vision error (Main LED): {e}")
        return 'UNKNOWN'

def get_success_led_state(bbox):
    """
    MODIFIED: Analyzes the success slot area (Slot 4)
    looking for bright green pixels (Area scanning logic from the TURBO script).
    Returns: True if enough green is detected, False otherwise.
    """
    if not bbox: return False
    try:
        width = bbox[2] - bbox[0]
        height = bbox[3] - bbox[1]
        if width <= 0 or height <= 0: return False

        # Capture the region
        img = pyautogui.screenshot(region=(bbox[0], bbox[1], width, height))

        green_count = 0
        # Skip 2 pixels at a time to be faster in analysis
        for x in range(0, width, 2):
            for y in range(0, height, 2):
                r, g, b = img.getpixel((x, y))

                # Criteria: Dominant and high green (copied from rf_helper_enprogreso)
                if g > 90 and g > (r + 30) and g > (b + 30):
                    green_count += 1
                    # If we found 5 pixels, it's enough (speed optimization)
                    if green_count > 5: return True
        return False

    except Exception as e:
        print(f"Vision error (Slot 4): {e}")
        return False

# =================================================================================
# --- SOLVER LOGIC (DOUBLE STAGE) ---
# =================================================================================

def restore_sequence(sequence):
    """Re-enters the known sequence to return to the current point."""
    if not sequence: return
    print(f"   [Restoring progress] Re-entering {len(sequence)} steps...")

    # --- INITIAL WAIT TIME FOR THE ERROR ANIMATION ---
    time.sleep(0.1)

    for move in sequence:
        tap_key(move)
        # Time between keys when restoring
        time.sleep(0.1)

    # Wait for the light to turn RED confirming we are ready
    time.sleep(0.1)

def bot_loop():
    global running, status_label

    possible_inputs = ['up', 'right', 'down', 'left']
    # Reset to 4 so Stage 1 has 4 steps.
    stages_targets = [4, 7]
    current_stage_idx = 0
    discovered_sequence = []

    print("--- STARTING SOLVER (Double Stage: 4 and 7) ---")

    if not focus_game_window():
        running = False; status_label.config(text="Error: No Focus"); return

    # --- CONFIGURATION VALIDATION ---
    if current_stage_idx == 0 and not stage1_success_boundary:
        messagebox.showwarning("Missing Data", "The 'Success Slot Area (Stage 1)' is mandatory for the first stage.")
        running = False; status_label.config(text="STOPPED", fg="red"); return


    while running:
        target_length = stages_targets[current_stage_idx]
        current_step = len(discovered_sequence) + 1

        # --- CHECK IF STAGE IS COMPLETE ---
        if current_step > target_length:
            print(f"--- STAGE {current_stage_idx + 1} COMPLETE! ---")

            # If Stage 1 (4 steps) is complete
            if current_stage_idx == 0:
                # Success was already detected in step 4, proceed to transition.

                status_label.config(text=f"Stage {current_stage_idx + 1} OK. Processing Stage 2", fg="green")

                print("Waiting 2 seconds for the next level...")
                time.sleep(2.0)

                current_stage_idx += 1
                discovered_sequence = [] # Reset sequence for the new stage
                print(f"Starting Stage {current_stage_idx + 1}")
                continue

            # If Stage 2 (7 steps) is complete
            elif current_stage_idx == 1:
                status_label.config(text="BOMB DISARMED!", bg="green", fg="white")
                running = False; break

        # ----------------------------------------------------------------------

        print(f"[Level {current_stage_idx + 1}] Looking for step {current_step}/{target_length}...")

        # Determine which detection method to use
        use_success_detection = (current_stage_idx == 0 and current_step == 4) # KEY CASE

        step_found = False

        for candidate in possible_inputs:
            if not running: break

            # --- 1. PRE-VALIDATION (Restoration Logic) ---
            # Only apply restoration logic if it's not step 4 of Stage 1
            if not use_success_detection:
                state = get_led_state(led_boundary)
                if len(discovered_sequence) > 0 and state != 'RED':
                    print(f"   [Protection] LED is not RED (it's {state}). Restoring sequence...")
                    restore_sequence(discovered_sequence)

                    # Check again to ensure it turned Red after restoring
                    time.sleep(0.1)
                    if get_led_state(led_boundary) != 'RED':
                        print("   [Alert] Unstable restoration, retrying...")
                        continue

            # --- 2. TEST CANDIDATE ---
            print(f" -> Testing: {candidate}")
            tap_key(candidate)

            # --- 3. WAIT FOR RESULT ---
            time.sleep(0.1)

            # --- 4. ANALYZE ---

            if use_success_detection:
                # Logic for STEP 4 (Using Success Slot/Quick Test)
                success_detected = get_success_led_state(stage1_success_boundary)

                if success_detected:
                    # Success: The step is correct, and Stage 1 is complete
                    print(f" -> CORRECT! ({candidate}) - Slot 4 detected as GREEN.")
                    discovered_sequence.append(candidate)
                    step_found = True
                    time.sleep(0.5) # Extra pause to ensure final detection
                    break
                else:
                    # Failure: Restore the sequence (Slot 4 not detected as GREEN)
                    print(f" -> Failed ({candidate}). Slot 4 not detected. Restoring...")
                    time.sleep(0.1)
                    restore_sequence(discovered_sequence)

            else:
                # Logic for STEPS 1, 2, 3 and Stage 2 (Using Main LED)
                new_state = get_led_state(led_boundary)

                if new_state == 'RED':
                    # Success: The step is correct, progress advances
                    print(f" -> CORRECT! ({candidate}) - LED is RED.")
                    discovered_sequence.append(candidate)
                    step_found = True
                    time.sleep(0.1)
                    break

                elif new_state == 'GREEN':
                    # Failure: The LED turns green, the bot will have to restore
                    print(f" -> Failed ({candidate}). GREEN detected. Waiting for animation...")

                    # Pause after failure for the error animation to complete
                    time.sleep(0.1)


                else:
                    print(f" -> Failed or Uncertain ({candidate}). State: {new_state}")
                    time.sleep(0.1)

        if not step_found and running:
            # If step 4 fails, the bot will try the next candidate.
            # For steps 1-3 and Stage 2, this should not happen.
            print("WARNING: Cycle without success. Retrying...")
            time.sleep(0.1)

# =================================================================================
# --- QUICK DEBUG FUNCTION (CONTINUOUS MONITORING) ---
# (SECTION REMOVED)
# =================================================================================

# =================================================================================
# --- GUI AND UTILITIES ---
# (This section does not require changes, except the final packaging part)
# =================================================================================

def focus_game_window():
    if not GAME_WINDOW_TITLE: return False
    try:
        win = gw.getWindowsWithTitle(GAME_WINDOW_TITLE)[0]
        if not win.isActive: win.activate(); time.sleep(1)
        return True
    except: return False

class SelectionWindow:
    def __init__(self, master, callback):
        self.master = master; self.callback = callback
        self.start_x = None; self.start_y = None; self.rect_id = None
        self.master.withdraw()
        self.window = tk.Toplevel(master)
        self.window.attributes('-alpha', 0.3, '-fullscreen', True, '-topmost', True)
        self.window.configure(bg='black')
        self.canvas = tk.Canvas(self.window, cursor="cross", bg='black', highlightthickness=0)
        self.canvas.pack(fill=tk.BOTH, expand=tk.YES)
        self.canvas.bind("<ButtonPress-1>", self.on_press); self.canvas.bind("<B1-Motion>", self.on_drag); self.canvas.bind("<ButtonRelease-1>", self.on_release)

    def on_press(self, event):
        self.start_x, self.start_y = event.x_root, event.y_root
        if self.rect_id: self.canvas.delete(self.rect_id)
        self.rect_id = self.canvas.create_rectangle(self.start_x, self.start_y, self.start_x, self.start_y, outline='#00ff00', width=2)

    def on_drag(self, event):
        if self.start_x: self.canvas.coords(self.rect_id, self.start_x, self.start_y, event.x_root, event.y_root)

    def on_release(self, event):
        if self.start_x:
            bbox = (min(self.start_x, event.x_root), min(self.start_y, event.y_root), max(self.start_x, event.x_root), max(self.start_y, event.y_root))
            self.window.destroy(); self.master.deiconify(); self.callback(bbox)

def set_led_area(): SelectionWindow(root, save_led_bbox)
def save_led_bbox(bbox):
    global led_boundary; led_boundary = bbox
    lbl_led_coords.config(text=f"Coordinates: {bbox}", fg="#008000")

def set_stage1_success_area(): SelectionWindow(root, save_stage1_success_bbox)
def save_stage1_success_bbox(bbox):
    global stage1_success_boundary; stage1_success_boundary = bbox
    lbl_success_coords.config(text=f"Coordinates: {bbox}", fg="#008000")

def on_press_key(key):
    global current_direction_to_set, listener
    try: k = key.char.lower()
    except: k = str(key).split('.')[-1].lower()

    # Special mapping for arrow keys or numeric keypad
    if k == 'up': k = 'up'
    elif k == 'down': k = 'down'
    elif k == 'left': k = 'left'
    elif k == 'right': k = 'right'
    elif hasattr(key, 'vk') and key.vk == 104: k = 'up'    # Numpad 8
    elif hasattr(key, 'vk') and key.vk == 98:  k = 'down'  # Numpad 2
    elif hasattr(key, 'vk') and key.vk == 100: k = 'left'  # Numpad 4
    elif hasattr(key, 'vk') and key.vk == 102: k = 'right' # Numpad 6

    key_bindings[current_direction_to_set] = k
    listener.stop()
    update_ui_keys()
    status_label.config(text="Key configured.")

def set_key(direction):
    global current_direction_to_set, listener
    current_direction_to_set = direction
    status_label.config(text=f"PRESS THE KEY FOR: {direction.upper()}")
    listener = keyboard.Listener(on_press=on_press_key); listener.start()

def update_ui_keys():
    # MODIFICATION: Change color to green if assigned, red if not.
    for d, k in key_bindings.items():
        txt = k.upper() if k else "---"
        color = "#008000" if k else "red"
        key_labels[d].config(text=f"Key: {txt}", fg=color)

def refresh_windows():
    try: wins = [w.title for w in gw.getAllWindows() if w.title.strip()]
    except: wins = []
    combo_windows['values'] = sorted(list(set(wins)))

def on_window_select(e): global GAME_WINDOW_TITLE; GAME_WINDOW_TITLE = combo_windows.get()

def start_bot():
    global running
    if not GAME_WINDOW_TITLE or not led_boundary or not stage1_success_boundary or any(v is None for v in key_bindings.values()):
        messagebox.showwarning("Missing Data", "Configure Game Window, LED Area, Slot Area 4, and the 4 Keys.")
        return
    running = True
    status_label.config(text="RUNNING...", fg="green")
    threading.Thread(target=bot_loop, daemon=True).start()

def stop_bot():
    global running
    running = False
    root.after(0, lambda: status_label.config(text="STOPPED", fg="red", bg="white"))

# --- GUI ---
# Title modified
root = tk.Tk(); root.title("Red Faction 1 Bomb Auto-Defuser v1.0"); root.geometry("500x420") # Reduced height

f_win = tk.Frame(root); f_win.pack(pady=5, fill='x')
tk.Label(f_win, text="Game Window:").pack(side='left', padx=5)
combo_windows = ttk.Combobox(f_win, state="readonly"); combo_windows.pack(side='left', fill='x', expand=True, padx=5)
combo_windows.bind("<<ComboboxSelected>>", on_window_select)
tk.Button(f_win, text="Refresh", command=refresh_windows).pack(side='left', padx=5)

tk.Label(root, text="--- VISION CONFIGURATION ---", font=("Arial", 8, "bold")).pack(pady=5)
# 1. LED for EACH step progress
tk.Button(root, text="SELECT LED AREA", command=set_led_area, bg="#eeeeee").pack(pady=2)
lbl_led_coords = tk.Label(root, text="Coordinates: (unassigned)", font=("Consolas", 8), fg="red"); lbl_led_coords.pack()

# 2. Success slot for Stage 1 (Used by the Bot)
tk.Button(root, text="SELECT SLOT 4 AREA", command=set_stage1_success_area, bg="#eeeeee").pack(pady=2)
lbl_success_coords = tk.Label(root, text="Coordinates: (unassigned)", font=("Consolas", 8), fg="red"); lbl_success_coords.pack()

# ----------------------------------------


tk.Label(root, text="--- KEY MAPPING ---", font=("Arial", 8, "bold")).pack(pady=5)
f_keys = tk.Frame(root); f_keys.pack(pady=5)
key_labels = {}
col = 0
for direction in ['up', 'down', 'left', 'right']:
    f = tk.Frame(f_keys, borderwidth=1, relief="groove"); f.grid(row=0, column=col, padx=5, pady=5)
    tk.Label(f, text=direction.upper(), font=("Arial", 10, "bold")).pack()
    # MODIFICATION: Initial color in red.
    key_labels[direction] = tk.Label(f, text="Key: ---", fg="red"); key_labels[direction].pack()
    tk.Button(f, text="Assign", command=lambda d=direction: set_key(d)).pack(pady=2)
    col += 1

tk.Button(root, text="START AUTO-DEFUSE", command=start_bot, bg="#ccffcc", height=2).pack(fill='x', padx=20, pady=10)
tk.Button(root, text="STOP", command=stop_bot, bg="#ffcccc").pack(fill='x', padx=20)
status_label = tk.Label(root, text="Ready"); status_label.pack(pady=5)

# Modified attribution:
# - fill='x' so it takes up the full available width.
# - anchor='e' so the text is aligned to the right (East).
tk.Label(root, text="by Eloy Farina", font=("Arial", 7)).pack(fill='x', anchor='e', padx=20)

root.after(500, refresh_windows)
root.mainloop()