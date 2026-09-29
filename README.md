# AUD-VDJ-MON-01 — Monitor de Master DJ + Alerta de CAudioLimiter

Monitor estéreo de pico digital, ligero y *Always-on-Top*, para supervisar el bus
maestro durante sesiones en directo con **Virtual DJ** sobre **Windows**.
Detecta la intervención de **CAudioLimiter** (el limitador de picos nativo del
motor de audio de Windows) que enmascara la saturación real ante los medidores
convencionales calibrados a 0.00 dBFS.

![Estado](https://img.shields.io/badge/versi%C3%B3n-1.2.0-00dd44)
![Python](https://img.shields.io/badge/python-%E2%89%A53.10-333333)
![Plataforma](https://img.shields.io/badge/plataforma-Windows%2010%2F11-333333)
![Licencia](https://img.shields.io/badge/licencia-MIT-ffb000)
![CI](https://img.shields.io/badge/tests-20%2F20-00dd44)

---

## ¿Por qué existe este medidor?

Los temas comerciales actuales salen de fábrica con picos entre **-0.5 y
-0.1 dBFS**. Al mezclar dos pistas a la vez (transición con ambos faders
abiertos), la suma —sobre todo en graves, 20–120 Hz— puede añadir entre
**+3 dB** (suma incoherente de potencias) y **+6 dB** (suma coherente en fase),
superando el techo digital.

Windows Audio Engine (**audiodg.exe**) trabaja internamente en coma flotante de
32 bits, pero el DAC USB solo acepta PCM entero. Para evitar el desbordamiento,
Microsoft inserta al final de la cadena **CAudioLimiter**, un limitador
*brickwall* que ancla la señal entre **≈ -0.14 y -0.30 dBFS**.

> **Consecuencia:** un medidor calibrado para clipear en `≥ 0.00 dBFS` *jamás*
> verá la saturación. El audio sale aplastado y bombeando, pero «técnicamente»
> nunca llega a 0 dBFS. Este proyecto dispara la alerta en **-0.30 dBFS**
> (0.966 lineal), justo donde actúa el limitador.

## Características (v1.1.0)

- **Medidor de 36 LEDs por canal** con escala segmentada verde/amarilla/roja
  según K-System, EBU R68 e IEC 60268-10.
- **Balística PPM real**: ataque instantáneo, caída exponencial de 12 dB/s y
  marcador *peak-hold* de 1 s.
- **Detección de clip con histéresis**: disparo en -0.30 dBFS y rearme bajo
  -1.0 dBFS — un evento por excursión, sin falsos positivos por micro-fluctuación.
- **Banner periférico de baliza** (parpadeo 4 Hz, retención 500 ms) + contador
  *latch* de eventos con reinicio.
- **Ventana elástica y Always-on-Top** pensada para cabina (oscuro, alto
  contraste, redibujado vectorial al redimensionar, DPI-aware).
- **Barra de estado con el endpoint real** monitorizado y tasa de muestreo.
- **Hilo de captura supervisado**: reintento con *backoff* exponencial y error
  visible en la GUI (nunca muere en silencio).
- **Fase 2 · Atenuación automática** *(v1.2.0)*: ante un clip sostenido ≥
  300 ms, el monitor pulsa un atajo de teclado nativo (`SendInput`, vía
  `ctypes`, **sin dependencias extra**) que Virtual DJ mapea a
  `master_volume -5%`. La corrección deja de depender del tiempo de reacción
  del operador.
- CLI completa: `--list`, `--device TEXTO`, `--panic-key ATAJO`,
  `--panic-hold-ms`, `--panic-cooldown-ms`.
- **Suite de 20 pruebas unitarias** (`pytest`) + CI en GitHub Actions
  (Windows + Linux, Python 3.10–3.13, `py_compile`, `ruff`).

## Requisitos

- Windows 10 / 11 de 64 bits
- Python ≥ 3.10 (el instalador de python.org incluye **Tkinter**)
- Un endpoint de reproducción con captura **WASAPI Loopback** (p. ej.
  *Altavoces (USB Audio CODEC)*)

## Instalación

```powershell
git clone https://github.com/60vueltasalsol/audio_vdj_monitor.git
cd aud-vdj-mon-01
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

## Uso

```powershell
python aud_vdj_mon_01.py                 # monitor sobre el endpoint por defecto
python aud_vdj_mon_01.py --list          # lista endpoints loopback disponibles
python aud_vdj_mon_01.py --device "USB"  # elige el endpoint cuyo nombre contenga "USB"
python aud_vdj_mon_01.py --panic-key "CTRL+F12"   # Fase 2: atenuación automática
```

O con el lanzador de doble clic (prelista los endpoints y propaga argumentos):

```powershell
.\run_monitor.bat --panic-key "CTRL+F12"
```

Coloca la ventana en una esquina visible del segundo monitor (o encima de
Virtual DJ: siempre flota). En **Audio Setup** de Virtual DJ selecciona la
tarjeta en modo **WASAPI compartido** o **DirectSound**.

### Fase 2 · Configuración de la atenuación automática

1. Elige un atajo libre, p. ej. `CTRL+F12`, y lánzalo con `--panic-key`.
2. En Virtual DJ: **Settings → Options → Keyboard**, asigna ese atajo a la
   acción `master_volume -5%`.
3. Comportamiento: si el clip se sostiene **300 ms** (`--panic-hold-ms`), se
   envía **una** pulsación; mientras persista la saturación se repite como
   máximo cada **2 s** (`--panic-cooldown-ms`). El contador
   `Pánico CTRL+F12: N` documenta cada intervención en la GUI.

> ⚠️ Si Virtual DJ usa un driver **ASIO exclusivo**, el audio no pasa por
> `audiodg.exe` y el *loopback* de WASAPI queda mudo. El medidor mostrará
> `-60.0 dB` aunque haya sonido.

## Calibración y protocolo de cabina

| Fase | Acción |
| :--- | :----- |
| **Prueba de sonido** | Reproduce una pista de referencia en el Deck 1 con ganancia unitaria. Ajusta la ganancia de canal para que los picos caigan en la **zona amarilla intermedia** (-9 a -6 dBFS). |
| **Mezcla en directo** | Al entrar el Deck 2, aplica *EQ Bass Swap* (corta los graves del tema entrante o saliente). Si el banner parpadea en rojo, atenúa -2/-3 dB o recorta LOW de inmediato. |
| **Post-sesión** | Un contador de clips **= 0** certifica una sesión sin intervención del limitador de Windows ni distorsión aguas abajo. |

## Arquitectura

```
[Virtual DJ · Deck 1 + Deck 2]
              │  (32-bit float)
              ▼
[Windows Audio Engine · audiodg.exe]
              ├─► APOs de endpoint (Equalizer APO · EQ/FIR/IIR/Preamp)
              ├─► CAudioLimiter (brickwall ≈ -0.14/-0.30 dBFS)
              ▼
[WASAPI Loopback] ◄── aud_vdj_mon_01.py
              │  (decimación Float32 → PCM 16/24)
              ▼
[USB Audio CODEC → DAC analógico]
```

Dos hilos desacoplados: **captura** (daemon, bloques de 2048 tramas ≈ 42.7 ms,
NumPy, sin E/S) y **GUI** (Tkinter a 40 Hz). La GUI nunca bloquea al audio y
ninguna excepción de captura mata la aplicación: se publica en la barra de
estado y se reintenta.

## Escala del vúmetro

| Zona       | Rango                 | LEDs        | Resolución | Significado operativo            |
| :--------- | :-------------------- | :---------- | :--------- | :------------------------------- |
| Verde      | -60.0 a -12.0 dBFS    | 20 (00–19)  | 2.4 dB/LED | Nominal / segura                 |
| Amarilla   | -12.0 a -3.0 dBFS     | 10 (20–29)  | 0.9 dB/LED | Margen (*headroom*) de mezcla    |
| Roja       | -3.0 a 0.0 dBFS       | 6 (30–35)   | 0.5 dB/LED | Crítica: limitador inminente     |
| Baliza     | ≥ -0.30 dBFS          | banner      | —          | CAudioLimiter actuando           |

## Limitaciones conocidas

- El *loopback* captura la señal **posterior** a la mezcla de Windows y a los
  APOs: es exactamente lo que llega al DAC, pero si Equalizer APO aplica un
  *preamp* negativo, la huella del limitador puede quedar atenuada por debajo
  del umbral de -0.30 dBFS. **Recomendación:** mantener el preamp de
  Equalizer APO en 0 dB durante la sesión.
- No es compatible con rutas **ASIO exclusivas** (ver nota de uso).
- La detección infiere la actuación de CAudioLimiter desde el techo de nivel;
  la confirmación analítica por *crest factor* está en el roadmap.

## Estructura del repositorio

```
aud-vdj-mon-01/
├─ aud_vdj_mon_01.py          # monitor completo (GUI + captura + Fase 2)
├─ run_monitor.bat            # lanzador de doble clic para cabina
├─ requirements.txt           # soundcard · numpy
├─ pyproject.toml             # metadatos, extras dev, ruff/pytest
├─ tests/
│  └─ test_core.py            # 20 pruebas de la lógica pura (sin hardware)
├─ docs/
│  └─ PROYECTO_EJECUTIVO.md   # documento ejecutivo v1.2.0
└─ .github/
   ├─ workflows/ci.yml        # matriz Win/Linux × Py 3.10-3.13 + ruff
   └─ ISSUE_TEMPLATE/bug_report.yml
```

## Pruebas y calidad

```powershell
pip install -r requirements.txt pytest
pytest -q                    # 20/20: calibración, escala, histéresis,
                             # balística, atajos de pánico, física §2.1
python -m py_compile aud_vdj_mon_01.py
```

## Roadmap

- [ ] Detector de aplastamiento por *crest factor* (techo plano sostenido).
- [x] ~~Acción correctiva automática vía MIDI/atajo de teclado~~ **(v1.2.0, atajo de teclado `SendInput`)**.
- [ ] Salida MIDI nativa opcional (`mido`/`rtmidi`) para controladores.
- [ ] Registro CSV de eventos de clip con marca temporal.
- [ ] Empaquetado `.exe` con PyInstaller.

## Documentación

- [`docs/PROYECTO_EJECUTIVO.md`](docs/PROYECTO_EJECUTIVO.md) — proyecto
  ejecutivo completo y corregido (v1.2.0).
- [`CHANGELOG.md`](CHANGELOG.md) — matriz de correcciones de auditoría F-01…F-11
  y registro de la Fase 2.
- [`CONTRIBUTING.md`](CONTRIBUTING.md) — cómo proponer cambios y pruebas de humo.

## Licencia

[MIT](LICENSE) — libre para usar, modificar y volar en cabina.
