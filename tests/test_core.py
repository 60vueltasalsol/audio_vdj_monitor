# -*- coding: utf-8 -*-
"""
Pruebas unitarias de la lógica pura de aud_vdj_mon_01 (v1.2.0).

No requieren hardware de audio: el módulo `soundcard` se sustituye por un
stub antes de importar el script, que solo se ejecuta bajo
``if __name__ == "__main__"`` — la GUI nunca se instancia en las pruebas.

    pytest -q
"""
from __future__ import annotations

import importlib.util
import math
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

# Stub de soundcard ANTES de importar el monitor (no hay MediaFoundation en CI)
if "soundcard" not in sys.modules:
    _stub = types.ModuleType("soundcard")
    _stub.all_microphones = lambda include_loopback=True: []
    sys.modules["soundcard"] = _stub

# Stub de tkinter: las clases GUI solo lo referencian en tiempo de ejecución,
# así basta un módulo vacío para probar la lógica pura en runners Linux/macOS.
if "tkinter" not in sys.modules:
    try:  # pragma: no cover - depende del runner
        import tkinter  # noqa: F401
    except ImportError:
        sys.modules["tkinter"] = types.ModuleType("tkinter")

# Las funciones bajo prueba son puras (math, threading): numpy solo interviene
# en la captura. En CI real se instala desde requirements.txt; el stub permite
# ejecutar la suite en entornos sin wheel disponible.
try:  # pragma: no cover
    import numpy  # noqa: F401
except ImportError:  # pragma: no cover
    _np = types.ModuleType("numpy")
    _np.ravel = lambda a: a
    sys.modules.setdefault("numpy", _np)

spec = importlib.util.spec_from_file_location("aud_vdj_mon_01", ROOT / "aud_vdj_mon_01.py")
mon = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = mon  # requerido por la introspección de @dataclass
spec.loader.exec_module(mon)


# --------------------------------------------------------------------- #
#  Constantes de calibración                                            #
# --------------------------------------------------------------------- #
def test_clip_threshold_es_menos_030_dbfs():
    assert mon.CLIP_AMP == pytest.approx(0.966051, abs=1e-5)


def test_rearm_por_debajo_de_disparo():
    assert mon.REARM_AMP < mon.CLIP_AMP
    assert mon.REARM_AMP == pytest.approx(0.891251, abs=1e-5)


def test_escala_de_36_leds_en_tres_zonas_contiguas():
    assert mon.NUM_LEDS == 36
    led_fin = 0
    for _d0, _d1, led0, led1, *_c in mon.ZONES:
        assert led0 == led_fin          # zonas contiguas (led1 exclusivo)
        led_fin = led1
    assert led_fin == 36


# --------------------------------------------------------------------- #
#  amp_to_db — piso coherente [F-07]                                    #
# --------------------------------------------------------------------- #
def test_amp_to_db_piso_y_techo():
    assert mon.amp_to_db(0.0) == mon.MIN_DB          # guardia log10
    assert mon.amp_to_db(1e-12) == mon.MIN_DB        # silencio digital
    assert mon.amp_to_db(1.0) == pytest.approx(0.0)
    assert mon.amp_to_db(0.5) == pytest.approx(-6.0206, abs=1e-3)


def test_amp_to_db_nunca_sale_de_escala_en_silencio():
    for amp in (0.0, 1e-9, 1e-6, 1e-4):
        assert mon.amp_to_db(amp) >= mon.MIN_DB


# --------------------------------------------------------------------- #
#  db_to_slot / lit_segments — geometría de la escala [F-04]            #
# --------------------------------------------------------------------- #
def test_ticks_caen_exactos():
    assert mon.db_to_slot(-60.0) == pytest.approx(0.0)
    assert mon.db_to_slot(-30.0) == pytest.approx(12.5)
    assert mon.db_to_slot(-18.0) == pytest.approx(17.5)
    assert mon.db_to_slot(-12.0) == pytest.approx(20.0)
    assert mon.db_to_slot(-6.0) == pytest.approx(26.667, abs=1e-3)
    assert mon.db_to_slot(-3.0) == pytest.approx(30.0)
    assert mon.db_to_slot(0.0) == pytest.approx(36.0)


def test_db_to_slot_clampea_fuera_de_rango():
    assert mon.db_to_slot(-120.0) == pytest.approx(0.0)
    assert mon.db_to_slot(+6.0) == pytest.approx(36.0)


def test_lit_segments_en_fronteras_de_zona():
    assert mon.lit_segments(-60.0) == 0
    assert mon.lit_segments(-12.0) == 20     # verde completa
    assert mon.lit_segments(-3.0) == 30      # amarillo completo
    assert mon.lit_segments(0.0) == 36       # escala completa


# --------------------------------------------------------------------- #
#  Histéresis de clip [F-02]                                            #
# --------------------------------------------------------------------- #
def test_clip_sostenido_cuenta_un_solo_evento():
    st = mon.SharedState()
    for _ in range(50):  # 50 bloques con pumping sobre el umbral
        st.publish_overload(mon.CLIP_AMP + 0.001)
        st.publish_overload(mon.CLIP_AMP - 0.002)
    # nunca cayó por debajo del rearme (-1.0 dBFS) → una sola excursión
    assert st.clip_count == 1


def test_rearme_habilita_el_siguiente_evento():
    st = mon.SharedState()
    st.publish_overload(mon.CLIP_AMP + 0.01)
    st.publish_overload(mon.REARM_AMP - 0.05)   # zanja bajo -1.0 dBFS
    st.publish_overload(mon.CLIP_AMP + 0.01)
    assert st.clip_count == 2


def test_reset_count_tambien_resetea_panico():
    st = mon.SharedState()
    st.publish_overload(mon.CLIP_AMP + 0.01)
    st.register_panic("CTRL+F12")
    st.reset_count()
    assert st.clip_count == 0 and st.panic_count == 0


# --------------------------------------------------------------------- #
#  Balística PPM [F-03]                                                 #
# --------------------------------------------------------------------- #
def test_ataque_instantaneo_y_caida_controlada():
    st = mon.SharedState()
    st.publish(0, mon.amp_to_db(0.5), now=1000.0)     # ≈ -6.02 dB
    alto = st.meters[0].display_db
    assert alto == pytest.approx(-6.0206, abs=1e-3)

    st.publish(0, mon.MIN_DB, now=1000.05)            # silencio: cae 12 dB/s
    esperado = max(mon.MIN_DB, alto - mon.RELEASE_DB_S * mon.BUFFER_SECONDS)
    assert st.meters[0].display_db == pytest.approx(esperado, abs=1e-9)


def test_peak_hold_se_retien_1s_y_cede_despues():
    st = mon.SharedState()
    st.publish(1, -6.0, now=100.0)
    st.publish(1, -30.0, now=100.5)                    # dentro del hold
    assert st.meters[1].hold_db == pytest.approx(-6.0)
    st.publish(1, -30.0, now=101.6)                    # hold expirado
    assert st.meters[1].hold_db == pytest.approx(
        max(-30.0, -6.0 - mon.RELEASE_DB_S * 2 * mon.BUFFER_SECONDS), abs=1e-9
    )


# --------------------------------------------------------------------- #
#  Fase 2 · parseo de atajos [P-01]                                     #
# --------------------------------------------------------------------- #
def test_parse_shortcut_validos():
    mods, key = mon.parse_shortcut("CTRL+F12")
    assert mods == [0x11] and key == 0x7B
    mods, key = mon.parse_shortcut("ctrl+shift+m")
    assert mods == [0x11, 0x10] and key == ord("M")


@pytest.mark.parametrize("malo", ["CTRL", "F13", "CTRL+META+A", "A+B", ""])
def test_parse_shortcut_invalidos(malo):
    with pytest.raises(ValueError):
        mon.parse_shortcut(malo)


# --------------------------------------------------------------------- #
#  Física de la suma documentada (F-06)                                 #
# --------------------------------------------------------------------- #
def test_suma_coherente_mas_6db_e_incoherente_mas_3db():
    assert 20 * math.log10(10 ** 0 + 10 ** 0) == pytest.approx(6.0206, abs=1e-3)
    assert 10 * math.log10(10 ** 0 + 10 ** 0) == pytest.approx(3.0103, abs=1e-3)
