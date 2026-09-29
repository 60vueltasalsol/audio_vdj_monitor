#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
========================================================================
 AUD-VDJ-MON-01 · Monitor estéreo de pico digital + alerta CAudioLimiter
========================================================================
 Versión : 1.2.0 (auditoría + Fase 2: acción correctiva automática)
 Entorno : Windows 10/11 x64 · Python 3.10+ · Virtual DJ · Equalizer APO
 Endpoint: Altavoces (USB Audio CODEC) vía WASAPI Loopback

 Correcciones de auditoría respecto a v1.0.0 (ver CHANGELOG.md):
   [F-01] Hilo de captura supervisado: reintento con backoff y estado de
          error visible (antes moría en silencio ante cualquier excepción).
   [F-02] Detección de clip con histéresis: disparo en -0.30 dBFS, rearme
          bajo -1.0 dBFS (el contador ya no cuenta falsos eventos).
   [F-03] Balística PPM según IEC 60268-10: ataque instantáneo, caída
          exponencial (12 dB/s) y marcador peak-hold de 1 s.
   [F-04] Regla de escala por interpolación zonal: ticks exactos (antes
          desplazados hasta 1.2 dB por índices de LED fijos).
   [F-05] Barra de estado con endpoint real monitorizado (antes el nombre
          de dispositivo se capturaba pero nunca se mostraba).
   [F-07] Piso logarítmico coherente: amp_to_db nunca sale de escala.
   [F-08] Cierre ordenado: señalizar + join del hilo antes de destruir.
   [F-09] Banner de baliza intermitente durante la retención de alerta.
   [F-10] DPI Awareness en Windows (texto nítido con escalado 125/150 %).

 Fase 2 (v1.2.0):
   [P-01] Atenuación automática ante clip sostenido: pulso de teclado
          nativo (SendInput vía ctypes, sin dependencias extra) hacia
          Virtual DJ. Mapear la tecla elegida en Virtual DJ →
          Settings → Options → Keyboard → «master_volume -5%».

 Uso:
   python aud_vdj_mon_01.py                 Monitor sobre el endpoint por defecto
   python aud_vdj_mon_01.py --list          Lista endpoints WASAPI Loopback
   python aud_vdj_mon_01.py --device "USB"  Selecciona endpoint por nombre
   python aud_vdj_mon_01.py --panic-key "CTRL+F12"
                                           Activa la atenuación automática
========================================================================
"""

from __future__ import annotations

import argparse
import math
import sys
import threading
import time
import warnings
from dataclasses import dataclass

import numpy as np
import soundcard as sc
import tkinter as tk

APP_VERSION = "1.2.0"

# Silenciar únicamente el warning conocido de MediaFoundation (búfer
# discontinuo). El resto de advertencias permanece visible en consola.
try:
    from soundcard import SoundcardRuntimeWarning
    warnings.filterwarnings("ignore", category=SoundcardRuntimeWarning)
except ImportError:  # pragma: no cover - versiones antiguas de soundcard
    pass

# --------------------------------------------------------------------- #
#  Parámetros de captura                                                 #
# --------------------------------------------------------------------- #
SAMPLE_RATE: int = 48_000                     # Hz (nátivo WASAPI Shared)
BUFFER_SIZE: int = 2_048                      # tramas/bloque ≈ 42.7 ms
BUFFER_SECONDS: float = BUFFER_SIZE / SAMPLE_RATE

# --------------------------------------------------------------------- #
#  Métrica digital                                                       #
# --------------------------------------------------------------------- #
MIN_DB: float = -60.0                         # piso de la escala
EPS: float = 1e-10                            # guardia numérica [F-07]

# Umbrales de sobrecarga sobre la cadena WASAPI Shared + CAudioLimiter.
# CAudioLimiter ancla la salida entre ≈ -0.14 y -0.30 dBFS, por lo que un
# medidor calibrado a 0.00 dBFS jamás vería la saturación (falso negativo).
CLIP_DB: float = -0.30                        # disparo de clip  [dBFS]
REARM_DB: float = -1.00                       # rearme de evento [dBFS] [F-02]
CLIP_AMP: float = 10.0 ** (CLIP_DB / 20.0)    # ≈ 0.9660
REARM_AMP: float = 10.0 ** (REARM_DB / 20.0)  # ≈ 0.8913

# Balística del vúmetro (IEC 60268-10) [F-03]
RELEASE_DB_S: float = 12.0                    # caída exponencial [dB/s]
PEAK_HOLD_MS: float = 1_000.0                 # retención del marcador de pico

# Interfaz
REFRESH_MS: int = 25                          # 40 Hz
ALERT_HOLD_MS: float = 500.0                  # retención del aviso
BLINK_MS: float = 125.0                       # fase de la baliza [F-09]

# --------------------------------------------------------------------- #
#  Escala segmentada de 36 LEDs (K-System / EBU R68 / IEC 60268-10)      #
# --------------------------------------------------------------------- #
# (db_min, db_max, led_inicial, led_final_excluido, color_on, color_off)
ZONES: tuple[tuple[float, float, int, int, str, str], ...] = (
    (-60.0, -12.0,  0, 20, "#00dd44", "#002409"),   # nominal   2.4 dB/LED
    (-12.0,  -3.0, 20, 30, "#ffbb00", "#2b2000"),   # headroom  0.9 dB/LED
    ( -3.0,   0.0, 30, 36, "#ff1111", "#2a0505"),   # crítica   0.5 dB/LED
)
NUM_LEDS: int = ZONES[-1][3]

SCALE_TICKS_DB: tuple[float, ...] = (-60.0, -30.0, -18.0, -12.0, -6.0, -3.0, 0.0)
SCALE_TICK_LABELS: tuple[str, ...] = ("-60", "-30", "-18", "-12", "-6", "-3", "0")

CHANNEL_NAMES: tuple[str, str] = ("L", "R")


def amp_to_db(amp: float) -> float:
    """Amplitud lineal → dBFS con piso coherente a la escala [F-07].

    La versión 1.0.0 usaba la guardia ``peak > 1e-5`` (≈ -100 dB) y luego
    mostraba en el display numérico valores por debajo del piso de escala
    (-60 dB), produciendo lecturas "fuera de instrumento". Aquí el piso
    de la conversión logarítmica y el de la escala son el mismo.
    """
    return max(20.0 * math.log10(max(float(amp), EPS)), MIN_DB)


def db_to_slot(db: float) -> float:
    """Posición continua 0..36 en la escala segmentada [F-04].

    Cada zona interpola linealmente en dB sobre sus ranuras LED. La regla
    inferior y el encendido de segmentos comparten esta misma función de
    transferencia, con lo que los ticks quedan exactamente alineados con
    la zona que describen (error 0 dB; en v1.0.0 era de hasta 1.2 dB).
    """
    db = max(MIN_DB, min(0.0, db))
    for z, (db_min, db_max, led0, led1, *_c) in enumerate(ZONES):
        if db <= db_max or z == len(ZONES) - 1:
            t = (db - db_min) / (db_max - db_min)
            return led0 + t * (led1 - led0)
    return float(NUM_LEDS)  # pragma: no cover - inalcanzable tras el clamp


def lit_segments(db: float) -> int:
    """Nº de LEDs encendidos para un nivel dado.

    Equivalente a ``np.sum(LED_THRESHOLDS <= db)`` de v1.0.0, pero
    derivado de la función zonal (sin tablas redundantes que diverjan).
    """
    return int(min(NUM_LEDS, max(0, math.floor(db_to_slot(db)))))


# --------------------------------------------------------------------- #
#  Fase 2 · Acción correctiva automática (pulso de teclado nativo)        #
# --------------------------------------------------------------------- #
MOD_VK = {"CTRL": 0x11, "ALT": 0x12, "SHIFT": 0x10, "WIN": 0x5B}
KEY_VK = {
    **{f"F{i}": 0x6F + i for i in range(1, 13)},            # F1..F12
    **{chr(c): c for c in range(ord("A"), ord("Z") + 1)},   # A..Z
    **{str(d): ord(str(d)) for d in range(10)},             # 0..9
    "SPACE": 0x20, "ESC": 0x1B, "ENTER": 0x0D, "TAB": 0x09,
    "UP": 0x26, "DOWN": 0x28, "LEFT": 0x25, "RIGHT": 0x27,
    "HOME": 0x24, "END": 0x23, "PGUP": 0x21, "PGDN": 0x22,
    "INS": 0x2D, "DEL": 0x2E,
}


def parse_shortcut(shortcut: str) -> tuple[list[int], int]:
    """'CTRL+F12' → ([vk_ctrl], vk_f12) con validación temprana.

    Se valida en ``main()`` antes de abrir la GUI para fallar rápido con
    un mensaje claro si el operador escribe una combinación no soportada.
    """
    parts = [p.strip().upper() for p in shortcut.split("+") if p.strip()]
    mods = [MOD_VK[p] for p in parts if p in MOD_VK]
    keys = [KEY_VK[p] for p in parts if p in KEY_VK]
    if len(parts) != len(mods) + len(keys) or len(keys) != 1:
        raise ValueError(
            f"Atajo no soportado: '{shortcut}'. "
            "Formato: [CTRL+][ALT+][SHIFT+][WIN+] + F1..F12 / A..Z / 0..9."
        )
    return mods, keys[0]


def send_shortcut(shortcut: str) -> None:
    """Pulsa un atajo de teclado a nivel de sistema con SendInput (Win32).

    Sin dependencias externas: se usan estructuras ctypes sobre user32.
    Virtual DJ recibe el atajo aunque esté en segundo plano si tiene el
    foco; en cabina se recomienda dejar Virtual DJ como ventana activa.
    """
    if sys.platform != "win32":
        raise NotImplementedError("La acción de pánico requiere Windows.")

    import ctypes
    from ctypes import wintypes

    mods, key = parse_shortcut(shortcut)

    ULONG_PTR = wintypes.WPARAM
    INPUT_KEYBOARD = 1
    KEYEVENTF_KEYUP = 0x0002

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [
            ("wVk", wintypes.WORD),
            ("wScan", wintypes.WORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ULONG_PTR),
        ]

    class MOUSEINPUT(ctypes.Structure):
        _fields_ = [
            ("dx", wintypes.LONG), ("dy", wintypes.LONG),
            ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR),
        ]

    class HARDWAREINPUT(ctypes.Structure):
        _fields_ = [
            ("uMsg", wintypes.DWORD),
            ("wParamL", wintypes.WORD),
            ("wParamH", wintypes.WORD),
        ]

    class INPUT_UNION(ctypes.Union):
        _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT), ("hi", HARDWAREINPUT)]

    class INPUT(ctypes.Structure):
        _fields_ = [("type", wintypes.DWORD), ("union", INPUT_UNION)]

    def stroke(vk: int, flags: int = 0) -> INPUT:
        event = INPUT()
        event.type = INPUT_KEYBOARD
        event.union.ki = KEYBDINPUT(vk, 0, flags, 0, 0)
        return event

    sequence = [stroke(vk) for vk in mods]
    sequence.append(stroke(key))
    sequence.append(stroke(key, KEYEVENTF_KEYUP))
    sequence.extend(stroke(vk, KEYEVENTF_KEYUP) for vk in reversed(mods))

    arr = (INPUT * len(sequence))(*sequence)
    sent = ctypes.windll.user32.SendInput(
        len(sequence), arr, ctypes.sizeof(INPUT)
    )
    if sent != len(sequence):
        raise OSError("SendInput no entregó la secuencia completa.")


@dataclass
class PanicConfig:
    """Configuración de la acción correctiva automática [P-01]."""

    shortcut: str | None = None      # None = solo aviso visual (v1.1.0)
    hold_ms: float = 300.0           # clip sostenido antes de actuar
    cooldown_ms: float = 2_000.0     # mínimo entre pulsaciones


# --------------------------------------------------------------------- #
#  Estado compartido hilo de captura ↔ GUI                               #
# --------------------------------------------------------------------- #
@dataclass
class ChannelMeter:
    display_db: float = MIN_DB      # nivel tras aplicar balística
    hold_db: float = MIN_DB         # marcador peak-hold
    hold_ts: float = 0.0            # instante del último pico (monotonic)


class SharedState:
    """Publicación de telemetría entre hilos.

    Las escrituras individuales de atributos son atómicas en CPython por
    la GIL; el candado protege además las actualizaciones compuestas.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.running: bool = True
        self.device: str = "Conectando al endpoint…"
        self.error: str | None = None
        self.clip_count: int = 0
        self.overloaded: bool = False
        self._clip_latched: bool = False
        self.panic_count: int = 0
        self.panic_shortcut: str | None = None
        self.meters: list[ChannelMeter] = [ChannelMeter(), ChannelMeter()]

    # ---------- API del hilo de captura ----------

    def publish(self, ch: int, raw_db: float, now: float) -> None:
        """Aplica balística PPM y publica el nivel del canal [F-03].

        Ataque: instantáneo (un bloque de ≈ 43 ms cubre con creces los
        5 ms de integración que exige IEC 60268-10 para picos).
        Caída: lineal en dB (exponencial en amplitud), 12 dB/s — punto
        medio entre DIN Type I (≈ 14 dB/s) y BBC Type IIa (≈ 8.6 dB/s).
        """
        m = self.meters[ch]
        with self._lock:
            if raw_db >= m.display_db:
                m.display_db = raw_db
            else:
                m.display_db = max(
                    raw_db, m.display_db - RELEASE_DB_S * BUFFER_SECONDS
                )
            if (
                m.display_db >= m.hold_db
                or (now - m.hold_ts) * 1000.0 > PEAK_HOLD_MS
            ):
                m.hold_db, m.hold_ts = m.display_db, now

    def publish_overload(self, max_amp: float) -> None:
        """Detector de clip con histéresis [F-02].

        v1.0.0 se rearmaba en el primer bloque con un único sample por
        debajo de 0.965: una sobrecarga sostenida con micro-fluctuaciones
        contaba decenas de falsos eventos por segundo. Ahora el evento se
        dispara en -0.30 dBFS y solo se rearma cuando el pico del bloque
        completo cae por debajo de -1.0 dBFS.
        """
        with self._lock:
            self.overloaded = max_amp >= CLIP_AMP
            if self.overloaded and not self._clip_latched:
                self._clip_latched = True
                self.clip_count += 1
            elif max_amp < REARM_AMP and self._clip_latched:
                self._clip_latched = False

    def register_panic(self, shortcut: str) -> None:
        with self._lock:
            self.panic_count += 1
            self.panic_shortcut = shortcut

    def set_device(self, name: str) -> None:
        with self._lock:
            self.device = name

    def set_error(self, message: str | None) -> None:
        with self._lock:
            self.error = message

    # ---------- API de la GUI ----------

    def reset_count(self) -> None:
        with self._lock:
            self.clip_count = 0
            self.panic_count = 0

    def snapshot(self) -> dict:
        """Foto coherente del estado para el ciclo de repintado."""
        with self._lock:
            return {
                "db": [m.display_db for m in self.meters],
                "hold": [m.hold_db for m in self.meters],
                "overloaded": self.overloaded,
                "clip_count": self.clip_count,
                "device": self.device,
                "error": self.error,
                "panic_count": self.panic_count,
                "panic_shortcut": self.panic_shortcut,
            }


# --------------------------------------------------------------------- #
#  Captura WASAPI Loopback                                               #
# --------------------------------------------------------------------- #
def find_loopback(preferred: str | None = None):
    """Resuelve el micrófono loopback del endpoint de reproducción.

    Orden de resolución: (1) preferencia explícita ``--device``,
    (2) loopback del altavoz predeterminado (patrón oficial soundcard),
    (3) primer dispositivo loopback disponible.
    """
    mics = sc.all_microphones(include_loopback=True)
    if not mics:
        raise RuntimeError("No se encontraron dispositivos WASAPI Loopback.")

    if preferred:
        for m in mics:
            if preferred.lower() in m.name.lower():
                return m
        raise RuntimeError(f"--device '{preferred}' no coincide con ningún endpoint.")

    try:
        speaker = sc.default_speaker()
        return sc.get_microphone(id=str(speaker.name), include_loopback=True)
    except Exception:
        pass

    loopbacks = [m for m in mics if getattr(m, "isloopback", False)]
    if loopbacks:
        return loopbacks[0]
    raise RuntimeError("El endpoint activo no expone captura loopback.")


def _capture_loop(state: SharedState, panic: PanicConfig, preferred: str | None) -> None:
    mic = find_loopback(preferred)
    state.set_error(None)
    state.set_device(mic.name)  # [F-05] el nombre real llega a la GUI

    overload_since: float | None = None   # inicio del clip sostenido [P-01]
    last_panic = 0.0

    with mic.recorder(samplerate=SAMPLE_RATE) as rec:
        while state.running:
            data = rec.record(numframes=BUFFER_SIZE)
            if data is None or data.size == 0:
                continue

            if data.ndim == 2 and data.shape[1] >= 2:
                chans = (data[:, 0], data[:, 1])
            else:  # endpoint mono / un solo canal útil
                chans = (np.ravel(data),) * 2

            peaks = [float(np.max(np.abs(c))) for c in chans]
            now = time.monotonic()
            max_amp = max(peaks)
            for ch, peak in enumerate(peaks):
                state.publish(ch, amp_to_db(peak), now)
            state.publish_overload(max_amp)

            # ---- [P-01] Atenuación automática ante clip sostenido ----
            if panic.shortcut is not None:
                if max_amp >= CLIP_AMP:
                    if overload_since is None:
                        overload_since = now
                    held_ms = (now - overload_since) * 1000.0
                    cooled_ms = (now - last_panic) * 1000.0
                    if held_ms >= panic.hold_ms and cooled_ms >= panic.cooldown_ms:
                        try:
                            send_shortcut(panic.shortcut)
                            state.register_panic(panic.shortcut)
                            last_panic = now
                            overload_since = None
                        except Exception as exc:  # no tumbar la captura
                            state.set_error(f"PanicKey: {type(exc).__name__}: {exc}")
                else:
                    overload_since = None


def audio_capture_thread(
    state: SharedState, panic: PanicConfig, preferred: str | None
) -> None:
    """Hilo daemon de adquisición: nunca muere en silencio [F-01].

    Cualquier excepción (endpoint desconectado, driver reiniciado, modo
    exclusivo ASIO ocupando la tarjeta…) se publica en la barra de estado
    y se reintenta con backoff exponencial hasta 5 s.
    """
    backoff = 1.0
    while state.running:
        try:
            _capture_loop(state, panic, preferred)
            backoff = 1.0
        except Exception as exc:  # supervisor: toda falla es telemetría
            if not state.running:
                break
            state.set_error(f"{type(exc).__name__}: {exc}")
            state.set_device("Reintentando captura…")
            time.sleep(min(backoff, 5.0))
            backoff *= 2.0


# --------------------------------------------------------------------- #
#  Interfaz gráfica (Tkinter)                                            #
# --------------------------------------------------------------------- #
class DJMonitorApp:
    def __init__(self, root: tk.Tk, state: SharedState) -> None:
        self.root = root
        self.state = state
        self.root.title(f"AUD-VDJ-MON-01 · Monitor de Master v{APP_VERSION}")
        self.root.geometry("560x260")
        self.root.resizable(True, True)
        self.root.minsize(340, 180)
        self.root.attributes("-topmost", True)
        self.root.configure(bg="#121212")

        self._alert_until = 0.0
        self._build_ui()
        self._tick()

    # -------------------- Construcción --------------------

    def _build_ui(self) -> None:
        # 1. Banner de alerta periférica
        self.pnl_alert = tk.Frame(self.root, bg="#1a1a1a", height=30)
        self.pnl_alert.pack(fill="x", padx=8, pady=(6, 2))
        self.pnl_alert.pack_propagate(False)

        self.lbl_alert = tk.Label(
            self.pnl_alert,
            text="NIVEL MASTER: OK",
            font=("Segoe UI", 10, "bold"),
            fg="#00ff66",
            bg="#1a1a1a",
        )
        self.lbl_alert.pack(expand=True)

        # 2. Medidores elásticos L / R
        meters_frame = tk.Frame(self.root, bg="#121212")
        meters_frame.pack(padx=10, fill="both", expand=True, pady=2)

        self.canvases: list[tk.Canvas] = []
        self.lbl_vals: list[tk.Label] = []
        for name in CHANNEL_NAMES:
            canvas, lbl_val = self._create_channel_row(meters_frame, name)
            self.canvases.append(canvas)
            self.lbl_vals.append(lbl_val)

        # 3. Regla inferior de dB
        scale_frame = tk.Frame(self.root, bg="#121212")
        scale_frame.pack(padx=10, fill="x", pady=(1, 2))
        tk.Label(scale_frame, text="  ", bg="#121212", width=2).pack(side="left", padx=(0, 4))
        tk.Label(scale_frame, text="", bg="#121212", width=8).pack(side="right", padx=(4, 2))

        self.canvas_scale = tk.Canvas(
            scale_frame, width=10, height=14, bg="#121212", highlightthickness=0
        )
        self.canvas_scale.pack(side="left", fill="x", expand=True)
        self.canvas_scale.bind("<Configure>", lambda _e: self._draw_scale())

        # 4. Pie de página: contador + reinicio
        footer = tk.Frame(self.root, bg="#121212")
        footer.pack(fill="x", padx=10, pady=(2, 0))

        self.lbl_clips = tk.Label(
            footer,
            text="Clips detectados: 0",
            font=("Segoe UI", 8, "bold"),
            fg="#ff4444",
            bg="#121212",
        )
        self.lbl_clips.pack(side="left")

        btn_reset = tk.Button(
            footer,
            text="Reiniciar contador",
            font=("Segoe UI", 8),
            command=self.state.reset_count,
            bg="#2a2a2a",
            fg="#ffffff",
            relief="flat",
            padx=5,
            pady=0,
        )
        btn_reset.pack(side="right")

        # 5. Barra de estado con endpoint real [F-05]
        self.lbl_status = tk.Label(
            self.root,
            text="Conectando al endpoint…",
            font=("Consolas", 7),
            fg="#6f6f6f",
            bg="#0b0b0b",
            anchor="w",
            padx=10,
        )
        self.lbl_status.pack(fill="x", side="bottom")

    def _create_channel_row(self, parent: tk.Frame, name: str):
        row = tk.Frame(parent, bg="#121212")
        row.pack(fill="both", expand=True, pady=2)

        tk.Label(
            row, text=name, font=("Segoe UI", 9, "bold"),
            fg="#ffffff", bg="#121212", width=2,
        ).pack(side="left", padx=(0, 4))

        lbl_val = tk.Label(
            row, text="-60.0 dB", font=("Consolas", 9, "bold"),
            fg="#00ffcc", bg="#121212", width=8,
        )
        lbl_val.pack(side="right", padx=(4, 2))

        canvas = tk.Canvas(
            row, width=10, height=10, bg="#080808",
            highlightthickness=1, highlightbackground="#222222",
        )
        canvas.pack(side="left", fill="both", expand=True)
        return canvas, lbl_val

    # -------------------- Dibujo --------------------

    def _geometry(self, canvas: tk.Canvas):
        w = canvas.winfo_width()
        h = canvas.winfo_height()
        margin_x = 4
        usable_w = max(1, w - margin_x * 2)
        slot_w = usable_w / NUM_LEDS
        seg_gap = max(1, int(slot_w * 0.18))
        seg_w = max(1, slot_w - seg_gap)
        return w, h, margin_x, slot_w, seg_gap, seg_w

    def _draw_scale(self) -> None:
        self.canvas_scale.delete("all")
        w, h, margin_x, slot_w, _gap, _sw = self._geometry(self.canvas_scale)
        if w <= 30:
            return
        for db, label in zip(SCALE_TICKS_DB, SCALE_TICK_LABELS):
            # [F-04] el tick se posiciona con la misma función zonal que
            # enciende los LEDs → alineación exacta con la escala.
            x = margin_x + db_to_slot(db) * slot_w
            anchor = "center"
            if db == MIN_DB:
                anchor = "w"
            elif db == 0.0:
                anchor = "e"
            self.canvas_scale.create_text(
                x, 7, text=label, fill="#777777",
                font=("Consolas", 7, "bold"), anchor=anchor,
            )

    def _draw_bar(self, canvas: tk.Canvas, db_val: float, hold_db: float) -> None:
        canvas.delete("all")
        w, h, margin_x, slot_w, seg_gap, seg_w = self._geometry(canvas)
        if w <= 30 or h <= 4:
            return

        y1 = max(1, int(h * 0.08))
        y2 = max(y1 + 2, int(h * 0.92))

        lit = lit_segments(db_val)
        hold_idx = lit_segments(hold_db) - 1 if hold_db > db_val + 0.25 else -1

        for i in range(NUM_LEDS):
            x1 = margin_x + i * slot_w
            x2 = x1 + seg_w
            on_color = off_color = ""
            for _db0, _db1, led0, led1, on_c, off_c in ZONES:
                if led0 <= i < led1:
                    on_color, off_color = on_c, off_c
                    break
            canvas.create_rectangle(
                x1, y1, x2, y2,
                fill=on_color if i < lit else off_color,
                outline="",
            )
            if i == hold_idx:  # [F-03] marcador peak-hold
                canvas.create_rectangle(
                    x1, y1, x2, y2, outline="#e8e8e8", width=1,
                )

    # -------------------- Bucle de UI (40 Hz) --------------------

    def _tick(self) -> None:
        snap = self.state.snapshot()
        now_ms = time.monotonic() * 1000.0

        for ch in (0, 1):
            self._draw_bar(self.canvases[ch], snap["db"][ch], snap["hold"][ch])
            self.lbl_vals[ch].config(text=f"{snap['db'][ch]:5.1f} dB")

        clips_txt = f"Clips detectados: {snap['clip_count']}"
        if snap["panic_shortcut"]:
            clips_txt += (
                f"   ·   Pánico {snap['panic_shortcut']}: {snap['panic_count']}"
            )
        self.lbl_clips.config(text=clips_txt)

        if snap["overloaded"]:
            self._alert_until = now_ms + ALERT_HOLD_MS

        if snap["error"] is not None:
            self.pnl_alert.config(bg="#3a2a00")
            self.lbl_alert.config(
                text="ERROR DE CAPTURA — REINTENTANDO…",
                fg="#ffbb00", bg="#3a2a00",
            )
        elif now_ms < self._alert_until:
            # [F-09] baliza intermitente a 4 Hz durante la retención
            phase = int(now_ms // BLINK_MS) % 2
            bg = "#cc0000" if phase == 0 else "#330000"
            fg = "#ffffff" if phase == 0 else "#ff6666"
            self.pnl_alert.config(bg=bg)
            self.lbl_alert.config(
                text="¡¡ ALERTA: MASTER SATURANDO (CLIP) !!", fg=fg, bg=bg,
            )
        else:
            self.pnl_alert.config(bg="#1a1a1a")
            self.lbl_alert.config(
                text="NIVEL MASTER: OK", fg="#00ff66", bg="#1a1a1a",
            )

        status = f"{snap['device']}   ·   {SAMPLE_RATE // 1000} kHz · WASAPI Loopback"
        if snap["panic_shortcut"]:
            status += f"  ·  pánico armado: {snap['panic_shortcut']}"
        if snap["error"] is not None:
            status = f"ERROR: {snap['error']}"
            self.lbl_status.config(fg="#ff5544")
        else:
            self.lbl_status.config(fg="#6f6f6f")
        self.lbl_status.config(text=f"{status}   ·   v{APP_VERSION}")

        self.root.after(REFRESH_MS, self._tick)


# --------------------------------------------------------------------- #
#  Arranque                                                              #
# --------------------------------------------------------------------- #
def enable_dpi_awareness() -> None:
    """Texto nítido en pantallas con escalado de Windows [F-10]."""
    if sys.platform != "win32":
        return
    import ctypes

    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PER_MONITOR_AWARE
        return
    except (AttributeError, OSError):
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except (AttributeError, OSError):
        pass


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="aud_vdj_mon_01",
        description="Monitor estéreo de pico digital con alerta de CAudioLimiter.",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="lista los endpoints WASAPI Loopback disponibles y termina",
    )
    parser.add_argument(
        "--device",
        metavar="TEXTO",
        default=None,
        help="selecciona el endpoint cuyo nombre contenga TEXTO",
    )
    parser.add_argument(
        "--panic-key",
        metavar="ATAJO",
        default=None,
        help=(
            "Fase 2: atajo enviado a Virtual DJ ante clip sostenido "
            "(p. ej. 'CTRL+F12'). Mapear el atajo en Virtual DJ a "
            "'master_volume -5%%'."
        ),
    )
    parser.add_argument(
        "--panic-hold-ms",
        type=float,
        default=300.0,
        help="milisegundos de clip sostenido antes de actuar (300 por defecto)",
    )
    parser.add_argument(
        "--panic-cooldown-ms",
        type=float,
        default=2000.0,
        help="milisegundos mínimos entre pulsaciones (2000 por defecto)",
    )
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)

    if args.list:
        mics = sc.all_microphones(include_loopback=True)
        loops = [m for m in mics if getattr(m, "isloopback", False)]
        if not loops:
            print("No se encontraron endpoints WASAPI Loopback.")
            return 1
        print("Endpoints WASAPI Loopback disponibles:")
        for m in loops:
            print(f"  · {m.name}")
        return 0

    panic = PanicConfig(
        shortcut=args.panic_key,
        hold_ms=args.panic_hold_ms,
        cooldown_ms=args.panic_cooldown_ms,
    )
    if panic.shortcut is not None:
        try:
            parse_shortcut(panic.shortcut)  # validación temprana
        except ValueError as exc:
            print(f"Error: {exc}")
            return 2
        if sys.platform != "win32":
            print("Error: --panic-key solo está disponible en Windows.")
            return 2

    enable_dpi_awareness()
    state = SharedState()
    if panic.shortcut:
        state.panic_shortcut = panic.shortcut

    worker = threading.Thread(
        target=audio_capture_thread,
        args=(state, panic, args.device),
        name="AudioCapture",
        daemon=True,
    )
    worker.start()

    root = tk.Tk()
    DJMonitorApp(root, state)

    def on_close() -> None:
        # [F-08] Cierre ordenado: señalizar al hilo, esperar a que suelte
        # el stream de MediaFoundation y solo entonces destruir la ventana.
        state.running = False
        worker.join(timeout=1.5)
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
