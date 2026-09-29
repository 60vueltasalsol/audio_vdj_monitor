# Changelog — AUD-VDJ-MON-01

Todas las correcciones siguen la matriz de auditoría F-01…F-11
(ver `docs/PROYECTO_EJECUTIVO.md`, Anexo A).

## [1.2.0] — 2025 · Fase 2: acción correctiva automática

### Añadido
- **[P-01] `--panic-key ATAJO`**: ante un clip sostenido ≥ 300 ms
  (`--panic-hold-ms`), el monitor envía una pulsación de teclado nativa a
  Virtual DJ mediante Win32 `SendInput` vía `ctypes` — **cero dependencias
  nuevas**. Repetición limitada por `--panic-cooldown-ms` (2 s por defecto).
  El contador `Pánico ATAJO: N` documenta cada intervención en la GUI y en la
  barra de estado; `Reiniciar contador` lo pone a cero junto con los clips.
- Validación temprana del atajo en `main()` (mensaje claro y salida con
  código 2 si el formato no es soportado o el SO no es Windows).
- `tests/test_core.py`: **20 pruebas** de la lógica pura (calibración,
  geometría de escala, histéresis, balística, parseo de atajos y física de la
  suma del §2.1). No requieren hardware: `soundcard`, `tkinter` y `numpy` se
  sustituyen por *stubs* cuando no están disponibles.
- `.github/workflows/ci.yml`: matriz Windows/Linux × Python 3.10–3.13 con
  `py_compile`, `pytest` y *job* de `ruff`.
- `.github/ISSUE_TEMPLATE/bug_report.yml`: formulario de fallo orientado a
  cabina (versión de Windows, endpoint, modo de salida de Virtual DJ, salida
  de `--list`).
- `pyproject.toml`: metadatos del proyecto, extras `dev` (pytest, ruff,
  pyinstaller) y configuración de pytest/ruff.
- `run_monitor.bat`: lanzador de doble clic que prelista los endpoints
  loopback y propaga argumentos al monitor.

### Cambiado
- `SharedState.reset_count()` ahora también reinicia el contador de pánico.
- La barra de estado muestra `pánico armado: ATAJO` cuando la Fase 2 está
  activa, y el título de la ventana incluye la versión (`APP_VERSION`).

## [1.1.0] — 2025 · Revisión de auditoría

### Corregido — Crítico
- **[F-01]** El hilo de captura moría en silencio ante cualquier excepción
  (`sc.default_speaker()`, endpoint desconectado, driver reiniciado, modo
  exclusivo ASIO) y la GUI quedaba congelada en «Conectando…». Ahora un
  bucle supervisor publica el error en la barra de estado y reintenta con
  *backoff* exponencial (1 → 5 s).
- **[F-02]** El detector de clip se rearmaba en el primer bloque con un único
  sample por debajo de 0.965: una sobrecarga sostenida con micro-fluctuaciones
  contaba decenas de falsos eventos por segundo y el banner parpadeaba a
  ~23 Hz. Ahora hay histéresis real: disparo en **-0.30 dBFS** y rearme cuando
  el pico del bloque completo cae bajo **-1.0 dBFS**.

### Corregido — Alto
- **[F-03]** El medidor mostraba el pico instantáneo de cada bloque (43 ms) sin
  integración ni caída: picos por debajo del umbral de persistencia visual
  eran invisibles (*falsos negativos visuales*) y el propio documento citaba
  IEC 60268-10 sin implementarla. Añadida balística PPM: ataque instantáneo,
  caída 12 dB/s y marcador *peak-hold* de 1 s por canal.
- **[F-04]** Los ticks de la regla se posicionaban con índices de LED fijos:
  `-30` caía en -28.8 dB, `-18` en -19.2 dB (error de lectura de 1.2 dB). Ahora
  la regla y los LEDs comparten `db_to_slot()`: interpolación lineal por zonas
  con ticks exactos.

### Corregido — Medio
- **[F-05]** `audio_state["device_name"]` se capturaba pero nunca se mostraba;
  imposible verificar qué endpoint se monitoriza. Añadida barra de estado con
  nombre real del loopback, tasa de muestreo y versión.
- **[F-06]** El proyecto ejecutivo usaba la fórmula de suma **incoherente**
  (potencias, +3 dB) mientras el texto describía interferencia **coherente** de
  fases (+6 dB). Documento corregido con ambas fórmulas y su dominio de
  validez (Anexo en `docs/PROYECTO_EJECUTIVO.md`).
- **[F-07]** La guardia logarítmica `peak > 1e-5` (≈ -100 dB) permitía que el
  display numérico mostrara valores por debajo del piso de escala (-60 dB).
  `amp_to_db()` ahora tiene piso coherente con el instrumento.
- **[F-08]** `sys.exit(0)` dentro del callback de Tkinter dejaba el stream de
  MediaFoundation en manos del GC; en algunos drivers el endpoint quedaba
  ocupado. Cierre ordenado: `running = False` → `join(timeout)` → `destroy()`.

### Corregido — Menor
- **[F-09]** El documento prometía «baliza intermitente» pero el código pintaba
  rojo fijo durante 500 ms. Añadido parpadeo de 4 Hz (fases de 125 ms).
- **[F-10]** Sin DPI Awareness la GUI se rasterizaba borrosa en pantallas con
  escalado 125/150 %. Añadida `SetProcessDpiAwareness` con *fallback*.

### Añadido
- CLI: `--list` y `--device TEXTO` para diagnóstico y selección de endpoint.
- `docs/PROYECTO_EJECUTIVO.md` actualizado a la spec implementada.

### Documentado (limitación, no defecto de código)
- **[F-11]** El punto de captura (WASAPI Loopback) es posterior a la mezcla de
  Windows y a los APOs. Si Equalizer APO aplica *preamp* negativo, la huella
  del limitador puede quedar bajo el umbral. Recomendación operativa: preamp
  de EQ APO en 0 dB. Detector por *crest factor* planificado en el roadmap.

## [1.0.0] — Versión inicial
- Captura WASAPI Loopback a 48 kHz / 2048 tramas.
- Medidor de 36 LEDs por canal, umbral de clip fijo en 0.965.
- Banner de alerta con retención de 500 ms, contador de clips, Always-on-Top.
