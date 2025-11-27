import tkinter as tk
from tkinter import messagebox, ttk
import threading
import time
from pynput import keyboard
import pygetwindow as gw 
import ctypes
import pyautogui 

# =================================================================================
# --- MOTOR DE INYECCIÓN DIRECTINPUT (BAJO NIVEL) ---
# =================================================================================

PUL = ctypes.POINTER(ctypes.c_ulong)
class KeyBdInput(ctypes.Structure): _fields_ = [("wVk", ctypes.c_ushort), ("wScan", ctypes.c_ushort), ("dwFlags", ctypes.c_ulong), ("time", ctypes.c_ulong), ("dwExtraInfo", PUL)]
class HardwareInput(ctypes.Structure): _fields_ = [("uMsg", ctypes.c_ulong), ("wParamL", ctypes.c_short), ("wParamH", ctypes.c_ushort)]
class MouseInput(ctypes.Structure): _fields_ = [("dx", ctypes.c_long), ("dy", ctypes.c_long), ("mouseData", ctypes.c_ulong), ("dwFlags", ctypes.c_ulong), ("time", ctypes.c_ulong), ("dwExtraInfo", PUL)]
class Input_I(ctypes.Union): _fields_ = [("ki", KeyBdInput), ("mi", MouseInput), ("hi", HardwareInput)]
class Input(ctypes.Structure): _fields_ = [("type", ctypes.c_ulong), ("ii", Input_I)]

# Códigos de escaneo (Scan Codes) para las teclas de dirección
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
    """Presiona una tecla mapeada rápidamente."""
    scan_code = SCAN_CODES.get(key_bindings.get(key_name))
    if scan_code:
        PressKey(scan_code)
        time.sleep(0.1) # Pulsación rápida
        ReleaseKey(scan_code)

# =================================================================================
# --- CONFIGURACIÓN GLOBAL ---
# =================================================================================

led_boundary = None  # Coordenada única del LED (Rojo/Verde para paso)
stage1_success_boundary = None # Casillero 4 (Usado por el Bot)
# test_boundary = None # Eliminado
key_bindings = { 'up': None, 'down': None, 'left': None, 'right': None } 
current_direction_to_set = None
listener = None
running = False
GAME_WINDOW_TITLE = None 

# =================================================================================
# --- VISIÓN: DETECCIÓN DE ESTADO DEL LED Y CASILLERO 4 (Escaneo de Píxeles) ---
# =================================================================================

def get_led_state(bbox):
    """
    Analiza el área del LED principal (Rojo=Éxito paso, Verde=Fallo/Reset/InicioNivel).
    Retorna: 'GREEN' (Fallo/Reset/InicioNivel), 'RED' (Activo/Éxito) o 'UNKNOWN'.
    """
    if not bbox: return 'UNKNOWN'
    try:
        width = bbox[2] - bbox[0]
        height = bbox[3] - bbox[1]
        if width <= 0 or height <= 0: return 'UNKNOWN'
        
        # Captura la región
        img = pyautogui.screenshot(region=(bbox[0], bbox[1], width, height))
        
        # Analizamos el centro
        cx, cy = width // 2, height // 2
        r, g, b = img.getpixel((cx, cy))
        
        if g > (r + 40): # Verde dominante
            return 'GREEN'
        if r > (g + 20): # Rojo dominante
            return 'RED'
            
        return 'UNKNOWN'
    except Exception as e:
        print(f"Error visión (LED principal): {e}")
        return 'UNKNOWN'

def get_success_led_state(bbox):
    """
    MODIFICADO: Analiza el área del casillero de éxito (Casilla 4)
    buscando píxeles verdes brillantes (Lógica de escaneo de áreas del script TURBO).
    Retorna: True si detecta suficiente verde, False si no.
    """
    if not bbox: return False
    try:
        width = bbox[2] - bbox[0]
        height = bbox[3] - bbox[1]
        if width <= 0 or height <= 0: return False
        
        # Captura la región
        img = pyautogui.screenshot(region=(bbox[0], bbox[1], width, height))
        
        green_count = 0
        # Saltamos de a 2 píxeles para ser más rápidos en el análisis
        for x in range(0, width, 2):
            for y in range(0, height, 2):
                r, g, b = img.getpixel((x, y))
                
                # Criterio: Verde dominante y alto (copiado de rf_helper_enprogreso)
                if g > 90 and g > (r + 30) and g > (b + 30):
                    green_count += 1
                    # Si ya encontramos 5 píxeles, es suficiente (optimización de velocidad)
                    if green_count > 5: return True
        return False
        
    except Exception as e:
        print(f"Error visión (Casillero 4): {e}") 
        return False

# =================================================================================
# --- LÓGICA DEL SOLVER (DOBLE ETAPA) ---
# =================================================================================

def restore_sequence(sequence):
    """Re-ingresa la secuencia conocida para volver al punto actual."""
    if not sequence: return
    print(f"   [Recuperando progreso] Re-ingresando {len(sequence)} pasos...")
    
    # --- TIEMPO DE ESPERA INICIAL PARA LA ANIMACIÓN DE ERROR ---
    time.sleep(0.1) 
    
    for move in sequence:
        tap_key(move)
        # Tiempo entre teclas al recuperar
        time.sleep(0.1) 
        
    # Esperamos a que la luz se ponga ROJA confirmando que estamos listos
    time.sleep(0.1)

def bot_loop():
    global running, status_label
    
    possible_inputs = ['up', 'right', 'down', 'left']
    # Reestablecemos a 4 para que la Etapa 1 tenga 4 pasos.
    stages_targets = [4, 7] 
    current_stage_idx = 0
    discovered_sequence = []
    
    print("--- INICIANDO SOLVER (Doble Etapa: 4 y 7) ---")
    
    if not focus_game_window():
        running = False; status_label.config(text="Error: Sin foco"); return
    
    # --- VALIDACIÓN DE CONFIGURACIÓN ---
    if current_stage_idx == 0 and not stage1_success_boundary:
        messagebox.showwarning("Faltan datos", "El 'Área Casillero Éxito (Etapa 1)' es obligatorio para la primera etapa.")
        running = False; status_label.config(text="DETENIDO", fg="red"); return


    while running:
        target_length = stages_targets[current_stage_idx]
        current_step = len(discovered_sequence) + 1
        
        # --- VERIFICAR SI COMPLETAMOS LA ETAPA ---
        if current_step > target_length:
            print(f"--- ¡ETAPA {current_stage_idx + 1} COMPLETADA! ---")
            
            # Si completó la Etapa 1 (4 pasos)
            if current_stage_idx == 0:
                # El éxito ya fue detectado en el paso 4, procedemos a la transición.
                
                status_label.config(text=f"Etapa {current_stage_idx + 1} OK. Procesando Etapa 2...", fg="green")
                
                print("Esperando 2 segundos para el siguiente nivel...")
                time.sleep(2.0)
                
                current_stage_idx += 1
                discovered_sequence = [] # Reinicia la secuencia para la nueva etapa
                print(f"Iniciando Etapa {current_stage_idx + 1}")
                continue
            
            # Si completó la Etapa 2 (7 pasos)
            elif current_stage_idx == 1:
                status_label.config(text="¡BOMBA DESACTIVADA!", bg="green", fg="white")
                running = False; break
        
        # ----------------------------------------------------------------------
        
        print(f"[Nivel {current_stage_idx + 1}] Buscando paso {current_step}/{target_length}...")
        
        # Determinamos qué método de detección usar
        use_success_detection = (current_stage_idx == 0 and current_step == 4) # CASO CLAVE
        
        step_found = False
        
        for candidate in possible_inputs:
            if not running: break
            
            # --- 1. VALIDACIÓN PREVIA (Lógica de Recuperación) ---
            # Solo aplica la lógica de restauración si no es el paso 4 de la Etapa 1
            if not use_success_detection:
                state = get_led_state(led_boundary)
                if len(discovered_sequence) > 0 and state != 'RED':
                    print(f"   [Protección] LED no está ROJO (es {state}). Restaurando secuencia...")
                    restore_sequence(discovered_sequence)
                    
                    # Volvemos a chequear para asegurar que quedó Rojo después de restaurar
                    time.sleep(0.1)
                    if get_led_state(led_boundary) != 'RED':
                        print("   [Alerta] Restauración inestable, reintentando...")
                        continue 
            
            # --- 2. PROBAR CANDIDATO ---
            print(f" -> Probando: {candidate}")
            tap_key(candidate)
            
            # --- 3. ESPERAR RESULTADO ---
            time.sleep(0.1) 
            
            # --- 4. ANALIZAR ---
            
            if use_success_detection:
                # Lógica para el PASO 4 (Usando Casillero Éxito/Test Rápido)
                success_detected = get_success_led_state(stage1_success_boundary)
                
                if success_detected:
                    # Éxito: El paso es correcto, y la Etapa 1 está completa
                    print(f" -> ¡CORRECTO! ({candidate}) - Casillero 4 detectado en VERDE.")
                    discovered_sequence.append(candidate)
                    step_found = True
                    time.sleep(0.5) # Pausa extra para asegurar la detección final
                    break 
                else:
                    # Fallo: Restauramos la secuencia (Casillero 4 no detectado en VERDE)
                    print(f" -> Falló ({candidate}). Casillero 4 no detectado. Restaurando...")
                    time.sleep(0.1)
                    restore_sequence(discovered_sequence)
                    
            else:
                # Lógica para los PASOS 1, 2, 3 y Etapa 2 (Usando LED Principal)
                new_state = get_led_state(led_boundary)
                
                if new_state == 'RED':
                    # Éxito: El paso es correcto, avanza el progreso
                    print(f" -> ¡CORRECTO! ({candidate}) - LED en ROJO.")
                    discovered_sequence.append(candidate)
                    step_found = True
                    time.sleep(0.1)
                    break 
                
                elif new_state == 'GREEN':
                    # Fallo: El LED se pone verde, el bot tendrá que restaurar
                    print(f" -> Falló ({candidate}). Detectado VERDE. Esperando animación...")
                    
                    # Pausa tras el fallo para que se complete la animación del error
                    time.sleep(0.1) 
                    
                
                else:
                    print(f" -> Falló o Incierto ({candidate}). Estado: {new_state}")
                    time.sleep(0.1)

        if not step_found and running:
            # Si el paso 4 falla, el bot intentará el siguiente candidato.
            # Para pasos 1-3 y Etapa 2, esto no debería ocurrir.
            print("ADVERTENCIA: Ciclo sin aciertos. Reintentando...")
            time.sleep(0.1)

# =================================================================================
# --- GUI Y UTILIDADES ---
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
    lbl_led_coords.config(text=f"Coordenadas: {bbox}", fg="#008000")

def set_stage1_success_area(): SelectionWindow(root, save_stage1_success_bbox)
def save_stage1_success_bbox(bbox): 
    global stage1_success_boundary; stage1_success_boundary = bbox
    lbl_success_coords.config(text=f"Coordenadas: {bbox}", fg="#008000")

def on_press_key(key):
    global current_direction_to_set, listener
    try: k = key.char.lower()
    except: k = str(key).split('.')[-1].lower()
    
    # Mapeo especial para teclas de flecha o teclado numérico
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
    status_label.config(text="Tecla configurada.")

def set_key(direction):
    global current_direction_to_set, listener
    current_direction_to_set = direction
    status_label.config(text=f"PRESIONA LA TECLA PARA: {direction.upper()}")
    listener = keyboard.Listener(on_press=on_press_key); listener.start()

def update_ui_keys():
    # MODIFICACIÓN: Cambia el color a verde si está asignado, rojo si no.
    for d, k in key_bindings.items():
        txt = k.upper() if k else "---"
        color = "#008000" if k else "red"
        key_labels[d].config(text=f"Tecla: {txt}", fg=color)

def refresh_windows():
    try: wins = [w.title for w in gw.getAllWindows() if w.title.strip()]
    except: wins = []
    combo_windows['values'] = sorted(list(set(wins)))

def on_window_select(e): global GAME_WINDOW_TITLE; GAME_WINDOW_TITLE = combo_windows.get()

def start_bot():
    global running
    if not GAME_WINDOW_TITLE or not led_boundary or not stage1_success_boundary or any(v is None for v in key_bindings.values()):
        messagebox.showwarning("Faltan datos", "Configura Ventana, Área del LED, Área del Casillero 4 y las 4 teclas.")
        return
    running = True
    status_label.config(text="EJECUTANDO...", fg="green")
    threading.Thread(target=bot_loop, daemon=True).start()

def stop_bot(): 
    global running
    running = False
    root.after(0, lambda: status_label.config(text="DETENIDO", fg="red", bg="white"))

# --- GUI ---
# Título modificado
root = tk.Tk(); root.title("Red Faction 1 Bomb Auto-Defuser v1.0"); root.geometry("500x420") # Reducida la altura

f_win = tk.Frame(root); f_win.pack(pady=5, fill='x')
tk.Label(f_win, text="Ventana Juego:").pack(side='left', padx=5)
combo_windows = ttk.Combobox(f_win, state="readonly"); combo_windows.pack(side='left', fill='x', expand=True, padx=5)
combo_windows.bind("<<ComboboxSelected>>", on_window_select)
tk.Button(f_win, text="Refrescar", command=refresh_windows).pack(side='left', padx=5)

tk.Label(root, text="--- CONFIGURACIÓN VISIÓN ---", font=("Arial", 8, "bold")).pack(pady=5)
# 1. LED para el progreso de CADA paso
tk.Button(root, text="SELECCIONAR ÁREA DEL LED", command=set_led_area, bg="#eeeeee").pack(pady=2)
lbl_led_coords = tk.Label(root, text="Coordenadas: (sin asignar)", font=("Consolas", 8), fg="red"); lbl_led_coords.pack()

# 2. Casillero de éxito para la Etapa 1 (Usado por el Bot)
tk.Button(root, text="SELECCIONAR ÁREA DEL CASILLERO 4", command=set_stage1_success_area, bg="#eeeeee").pack(pady=2)
lbl_success_coords = tk.Label(root, text="Coordenadas: (sin asignar)", font=("Consolas", 8), fg="red"); lbl_success_coords.pack()

# ----------------------------------------


tk.Label(root, text="--- MAPEO DE TECLAS ---", font=("Arial", 8, "bold")).pack(pady=5)
f_keys = tk.Frame(root); f_keys.pack(pady=5)
key_labels = {}
col = 0
for direction in ['up', 'down', 'left', 'right']:
    f = tk.Frame(f_keys, borderwidth=1, relief="groove"); f.grid(row=0, column=col, padx=5, pady=5)
    tk.Label(f, text=direction.upper(), font=("Arial", 10, "bold")).pack()
    # MODIFICACIÓN: Color inicial en rojo.
    key_labels[direction] = tk.Label(f, text="Tecla: ---", fg="red"); key_labels[direction].pack()
    tk.Button(f, text="Asignar", command=lambda d=direction: set_key(d)).pack(pady=2)
    col += 1

tk.Button(root, text="INICIAR AUTO-DEFUSE", command=start_bot, bg="#ccffcc", height=2).pack(fill='x', padx=20, pady=10)
tk.Button(root, text="DETENER", command=stop_bot, bg="#ffcccc").pack(fill='x', padx=20)
status_label = tk.Label(root, text="Listo"); status_label.pack(pady=5)

# Atribución modificada:
# - fill='x' para que ocupe todo el ancho disponible.
# - anchor='e' para que el texto se alinee a la derecha (East).
tk.Label(root, text="by Eloy Farina", font=("Arial", 7)).pack(fill='x', anchor='e', padx=20)

root.after(500, refresh_windows)

root.mainloop()
