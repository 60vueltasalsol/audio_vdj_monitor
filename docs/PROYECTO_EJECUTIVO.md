# PROYECTO EJECUTIVO DE SOFTWARE

## Sistema de Monitoreo Estéreo de Pico Digital, Detección de Saturación y Alerta Temprana de CAudioLimiter para Cabina DJ (Virtual DJ)

| Campo | Valor |
| :--- | :--- |
| **Código de proyecto** | AUD-VDJ-MON-01 |
| **Versión del documento** | 1.2.0 (auditoría + Fase 2 implementada) |
| **Entorno operativo** | Windows 10/11 x64 · Virtual DJ · Equalizer APO |
| **Dispositivo endpoint objetivo** | Altavoces (USB Audio CODEC) |
| **Lenguaje y entorno** | Python ≥ 3.10 (Tkinter · Soundcard · NumPy · ctypes) |

> **Nota edición 1.2.0.** Incorpora la especificación §4.6 —la acción
> correctiva automatizada que la edición 1.1.0 dejaba como trabajo futuro— ya
> implementada y probada (20/20 pruebas en `tests/`). La respuesta a las dos
> cuestiones operativas pendientes queda así cerrada: (1) el modo de salida
> documentado es WASAPI Shared/DirectSound —ASIO exclusivo es incompatible por
> arquitectura, §3.1—; (2) la atenuación automática se realiza por atajo de
> teclado nativo en lugar de MIDI, sin añadir dependencias al proyecto.

> **Nota de revisión.** Esta edición 1.1.0 corrige el documento original v1.0.0
> tras la auditoría de código y de marco técnico. Los cambios sustantivos son:
> (a) corrección de la fórmula de suma de señales del §2.1 —se distinguen los
> casos coherente e incoherente—; (b) incorporación de la balística PPM que la
> norma citada exige y que la implementación v1.0.0 omitía; (c) especificación
> de la detección de clip con histéresis; (d) corrección de la geometría de la
> regla de escala; y (e) declaración explícita de las limitaciones del punto de
> captura. El Anexo A traza cada corrección con su hallazgo de auditoría.

---

## 1. Resumen ejecutivo

El presente proyecto documenta el diseño, justificación teórica, arquitectura
técnica e implementación de una herramienta de software ligera, no intrusiva y
de baja latencia destinada a la supervisión visual del bus maestro de audio
durante sesiones en directo con Virtual DJ.

El sistema solventa la problemática recurrente en la que el operador DJ, al
realizar transiciones y mezclar dos pistas simultáneamente, satura el bus de
salida digital. La herramienta incorpora una calibración basada en los
estándares de audio digital profesional (K-System y normas EBU/IEC), una
detección con histéresis de la intervención destructiva de **CAudioLimiter**
(el limitador de pico nativo del motor de audio de Windows) y advertencias
visuales de alto impacto periférico en cabina mediante una interfaz elástica y
flotante (*Always on Top*).

## 2. Planteamiento del problema y justificación

### 2.1. Dinámica de suma de señales en mezcla DJ

Las canciones comerciales modernas producidas para streaming y club están
masterizadas con un margen dinámico muy reducido, cuyos picos máximos se sitúan
de fábrica entre **-0.5 dBFS y -0.1 dBFS**. Cuando el operador mantiene abiertos
los dos faders durante una transición, ambas señales se suman en el bus maestro.
El incremento resultante depende del grado de coherencia entre las fuentes:

**Caso coherente (señales en fase — peor caso posible):**

```
ΔL = 20 · log10( 10^(L1/20) + 10^(L2/20) )     →  +6.02 dB si L1 = L2
```

Aplicable cuando ambas pistas comparten energía en fase, lo cual ocurre de forma
sistemática en la banda de graves (20–120 Hz): los *kicks* de dos temas de club
están fuertemente correlacionados —misma región espectral, envolventes
similares, afinación cercana al tempo común— y sus longitudes de onda (2.9–17 m)
hacen imposible la decorrelación instantánea.

**Caso incoherente (señales estadísticamente independientes):**

```
ΔL = 10 · log10( 10^(L1/10) + 10^(L2/10) )     →  +3.01 dB si L1 = L2
```

Aplicable como valor esperado en bandas medias y agudas con material
no correlacionado.

**Conclusión operativa:** la suma instantánea en el bus se sitúa entre **+3 dB y
+6 dB** sobre el nivel de cada pista individual. Partiendo de temas masterizados
a ≈ -0.2 dBFS, el techo digital (0.0 dBFS) se supera con holgura en cuanto dos
canales suenan a plena ganancia — de ahí la necesidad de margen (*headroom*) y
de supervisión activa.

### 2.2. La distorsión en cabina y la falta de supervisión

En un entorno de directo (iluminación tenue, monitorización en auriculares,
interacción con el público), el DJ suele desatender los pequeños medidores de la
interfaz gráfica de Virtual DJ. Esto conduce a:

- Distorsión armónica no deseada en el sistema de amplificación (PA).
- Sobreexcursión térmica y mecánica de transductores (subwoofers y motores de
  agudos).
- Activación de limitadores aguas abajo, provocando fatiga auditiva en la
  audiencia.

## 3. Marco técnico: el subsistema de audio de Windows y CAudioLimiter

### 3.1. Cadena de renderizado en modo compartido (WASAPI Shared)

Para que Equalizer APO procese la señal antes del DAC, Virtual DJ debe operar a
través del subsistema de mezcla compartida de Windows (Windows Audio Engine).
La ruta de procesamiento:

```
[Virtual DJ (Deck 1 + Deck 2)]
              │
              ▼  (Audio 32-bit Float)
[Windows Audio Engine (audiodg.exe)]
              │
              ├─► Procesamiento APO (Equalizer APO: EQs, FIR/IIR, Preamp)
              │
              ├─► Módulo de protección: CAudioLimiter (Peak Limiting)
              │
              ▼
[Punto de captura: WASAPI Loopback] ◄── AUD-VDJ-MON-01
              │
              ▼  (Conversión Float32 → PCM 16/24)
[Controlador USB Audio CODEC → DAC analógico]
```

> **Advertencia de arquitectura:** si Virtual DJ se configura con un driver
> **ASIO exclusivo**, la señal no atraviesa `audiodg.exe`: el loopback WASAPI
> queda mudo y el medidor leerá silencio (-60 dBFS). Este documento asume
> WASAPI Shared o DirectSound.

### 3.2. Mecanismo de acción de CAudioLimiter

Documentado por ingenieros del equipo Windows Core Audio de Microsoft (p. ej.,
Matthew van Eerde), el proceso `audiodg.exe` gestiona la mezcla en un bus de
coma flotante de 32 bits (IEEE 754). Mientras la señal permanece en coma
flotante dentro de la memoria, técnicamente puede superar 0 dBFS sin distorsión
por desbordamiento numérico.

Sin embargo, al final de la cadena de efectos de endpoint (EFX), la señal debe
cuantizarse a formato de enteros fijos (PCM de 16 o 24 bits) para ser
transmitida al chip receptor de la tarjeta USB.

- **Sin limitador:** si una muestra excede el rango (-1.0, +1.0) al convertirse
  a enteros, ocurriría un desbordamiento catastrófico (*integer wrap-around* o
  *hard clipping*), emitiendo chasquidos de alta energía destructivos para los
  altavoces.
- **Con CAudioLimiter:** Microsoft introduce un limitador de pared de ladrillo
  (*brickwall limiter*) con tiempo de ataque ultrarrápido que detecta
  magnitudes próximas a escala completa y comprime dinámicamente la señal hacia
  abajo, anclándola en la práctica entre ≈ **-0.14 dBFS y -0.30 dBFS**.

> **Nota de precisión (v1.1.0):** la edición anterior afirmaba que el limitador
> actúa sobre valores que «exceden el 95–98 % de la escala completa». La cifra
> exacta de umbral interno no está publicada; lo observable a posteriori en el
> loopback es el **techo de salida** citado. El presente documento distingue
> entre *hecho medible* (techo ≈ -0.14/-0.30 dBFS) e *hipótesis de diseño*
> (umbral interno), y fundamenta la calibración únicamente en el hecho medible.

### 3.3. El efecto «falso negativo» y la necesidad de calibración

Un medidor de pico digital convencional programado para disparar clip en
`≥ 0.00 dBFS` jamás detectará la saturación provocada por Virtual DJ en Windows,
porque CAudioLimiter frena la señal artificialmente a ≈ -0.2/-0.3 dBFS. El
resultado audible es una señal aplastada, sin dinámica y con severo efecto de
bombeo (*pumping*), pero que «técnicamente» no llega a 0 dBFS.

Por ello, este proyecto establece el umbral de disparo de clip en
**-0.30 dBFS (0.966 en amplitud normalizada)**, con rearme de evento en
**-1.0 dBFS** (ver §4.4).

## 4. Especificaciones de la arquitectura de software

### 4.1. Desacoplamiento de hilos (concurrencia)

Para garantizar continuidad en el búfer (sin *underruns*):

- **Hilo de adquisición (daemon supervisado):** bucle continuo que lee tramas
  de audio mediante WASAPI Loopback (`blocksize = 2048` ≈ 42.7 ms a 48 kHz) y
  realiza operaciones vectorizadas con NumPy sin sobrecarga de E/S. Un bucle
  supervisor captura cualquier excepción, la publica en la barra de estado y
  reintenta con *backoff* exponencial (1 → 5 s) sin interrumpir la GUI.
- **Hilo principal (GUI):** interfaz con tasa de refresco constante de 40 Hz
  (25 ms), independiente del flujo de audio. La GUI solo lee un *snapshot*
  coherente del estado compartido (sección crítica protegida por candado).

### 4.2. Balística del medidor (IEC 60268-10)

La edición v1.0.0 mostraba el pico instantáneo de cada bloque sin ley temporal:
a 40 Hz de refresco, los picos breves quedaban por debajo del umbral de
persistencia de la visión humana (*falsos negativos visuales*). La presente
especificación implementa:

| Parámetro | Valor | Criterio |
| :--- | :--- | :--- |
| **Ataque** | instantáneo por bloque | Un bloque de 42.7 ms cubre los 5 ms de integración que IEC 60268-10 exige para registro completo de pico |
| **Caída** | 12 dB/s (lineal en dB) | Punto medio entre DIN Type I (≈ 14 dB/s) y BBC Type IIa (≈ 8.6 dB/s) |
| **Peak-hold** | 1 000 ms, marcador por canal | Persistencia del último pico relevado |

### 4.3. Calibración del vúmetro (escala segmentada de 36 LEDs)

El medidor utiliza una distribución zonificada que replica los estándares
analógicos/digitales (K-System, EBU R68 e IEC 60268-10). LEDs y regla inferior
comparten una única función de transferencia `db_to_slot()` —interpolación
lineal en dB dentro de cada zona—, lo que garantiza **error de alineación 0 dB**
entre marcas y segmentos (la v1.0.0 posicionaba la regla por índices fijos con
desviaciones de hasta 1.2 dB).

| Zona | Rango de amplitud | LEDs | Resolución | Comportamiento / norma |
| :--- | :--- | :--- | :--- | :--- |
| **Verde** | -60.0 a -12.0 dBFS | 20 (0–19) | 2.4 dB/LED | **Nominal / segura.** Nivel de reproducción de temas individuales. |
| **Amarilla** | -12.0 a -3.0 dBFS | 10 (20–29) | 0.9 dB/LED | **Margen (*Headroom*).** Entrada en suma de canales; alerta preventiva. |
| **Roja** | -3.0 a 0.0 dBFS | 6 (30–35) | 0.5 dB/LED | **Crítica.** Peligro inminente; el LED 35 representa el límite físico. |

```
[-60 dB]                 [-12 dB]        [-3 dB]    [0 dB]
 [||||||||||||||||||||]   [||||||||||]    [||||||]  [CLIP]
 ZONA VERDE (segura)       AMARILLA        ROJA
```

### 4.4. Detección de saturación con histéresis

La detección de eventos de clip implementa un ciclo disparo/rearme:

- **Disparo:** el pico del bloque alcanza `≥ -0.30 dBFS` (0.966 lineal) → se
  contabiliza **un** evento y se activa el aviso visual.
- **Rearme:** el evento solo puede volver a contabilizarse después de que el
  pico de un bloque completo caiga por debajo de `-1.0 dBFS`.

Justificación: la v1.0.0 se rearmaba en el primer bloque con un único sample bajo
umbral; una sobrecarga sostenida con micro-fluctuaciones (el propio *pumping*
del limitador modula el techo decimas de dB) contabilizaba decenas de falsos
eventos por segundo, invalidando el contador como métrica de auditoría de la
sesión y haciendo parpadear el banner a ~23 Hz. Con histéresis, el contador
mide **excursiones de ganancia reales**, que es el dato operativo relevante.

### 4.5. Motor gráfico elástico y funciones para cabina

- **Adaptabilidad:** renderizado vectorial sobre canvas relativos
  (`fill="both"`, `expand=True`) con redibujado ante `<Configure>`; la escala se
  recolcula desde la función zonal, sin posiciones absolutas.
- **Aviso de cabina (foco periférico):** banner superior de alto contraste que
  conmuta de verde esmeralda a **baliza roja intermitente** —parpadeo de 4 Hz
  (fases de 125 ms) durante una retención de 500 ms desde el último bloque
  saturado—. La intermitencia es deliberada: el sistema visual humano detecta
  estímulos parpadeantes en visión periférica con mayor fiabilidad que cambios
  de color sostenidos.
- **Ventana siempre visible** (`-topmost`): impide que Virtual DJ oculte la
  herramienta al maximizarse.
- **Barra de estado de endpoint:** muestra el nombre real del dispositivo
  loopback capturado, la tasa de muestreo y la versión — el operador verifica de
  un vistazo *qué* está monitorizando (crítico con varias tarjetas USB).
- **DPI Awareness:** llamada a `SetProcessDpiAwareness` en Windows para texto
  vectorial nítido en pantallas con escalado 125/150 %.
- **Contador de eventos latch:** registro acumulado por sesión con reinicio
  manual. Un valor 0 al final certifica operación sin intervención del
  limitador.
- **Cierre ordenado:** la señal de stop llega al hilo de captura *antes* de
  destruir la ventana, con `join(timeout=1.5 s)` para liberar el stream de
  MediaFoundation de forma determinista.

### 4.6. Acción correctiva automatizada (Fase 2)

El aviso visual transfiere la corrección al tiempo de reacción del operador
(0.5–2 s en condiciones de cabina). La Fase 2 cierra el lazo: el propio
monitor puede ordenar a Virtual DJ la atenuación del master.

**Principio de diseño:** cero dependencias nuevas y cero controladores. En
lugar de MIDI (que exigiría `rtmidi` y un puerto virtual), la acción se
implementa como **pulsación de teclado a nivel de sistema** mediante Win32
`SendInput` invocado con `ctypes` —la misma vía que usa cualquier macro de
teclado—, que Virtual DJ captura mediante su sistema de mapeo de atajos
(*Settings → Options → Keyboard*).

**Algoritmo** (ejecutado en el hilo de captura, tras evaluar cada bloque):

1. Si el bloque está en clip (`≥ -0.30 dBFS`), se inicia/continúa un cronómetro
   de sostenimiento; al caer bajo umbral, el cronómetro se anula.
2. Cumplido el tiempo de persistencia (`--panic-hold-ms`, **300 ms** por
   defecto), se envía **una única** pulsación del atajo configurado
   (`--panic-key`, p. ej. `CTRL+F12`) y se registra en el contador
   `Pánico ATAJO: N`, visible en la GUI como parte de la auditoría de sesión.
3. Mientras la saturación persista, la pulsación puede repetirse respetando el
   enfriamiento (`--panic-cooldown-ms`, **2 000 ms** por defecto): cada
   pulsación mapeada a `master_volume -5%` aplica una atenuación escalonada y
   revertible, en lugar de un corte abrupto.
4. La validación del atajo se realiza en `main()` antes de abrir la GUI:
   formato `[CTRL+][ALT+][SHIFT+][WIN+]` + `F1..F12` / `A..Z` / `0..9` /
   teclas de edición; un formato inválido aborta con mensaje claro y código 2.

**Propiedades de seguridad:** la acción es idempotente y acotada (nunca más de
una pulsación por ventana de enfriamiento); cualquier fallo de `SendInput` se
publica como telemetría en la barra de estado sin interrumpir la captura; y
con `--panic-key` ausente el sistema se comporta exactamente como la v1.1.0
(solo aviso visual).

## 5. Requisitos técnicos y matriz de compatibilidad

- **Sistema operativo:** Windows 10 / 11 (64 bits).
- **Controlador de audio:** endpoint con captura WASAPI Loopback (modo
  compartido; no compatible con rutas ASIO exclusivas).
- **Pila de software:**
  - Python ≥ 3.10 (Tkinter incluido en el instalador oficial / Tcl-Tk)
  - `soundcard` ≥ 0.4.2 (interoperabilidad CFFI con Windows MediaFoundation /
    Core Audio)
  - `numpy` ≥ 1.22.0 (cálculo vectorial de envolventes de pico)

## 6. Protocolo de operación en cabina (manual de uso DJ)

1. **Fase de calibración previa (prueba de sonido):**
   - Reproducir una pista de referencia en el Deck 1 con ganancia unitaria (0 dB).
   - Ajustar la ganancia de canal de Virtual DJ hasta que los picos más intensos
     se sitúen en la zona amarilla intermedia (-9 a -6 dBFS).
2. **Fase de mezcla (en directo):**
   - Al introducir la segunda pista en el Deck 2, aplicar la técnica de corte de
     graves (*EQ Bass Swap*), recortando la banda LOW del tema entrante o
     saliente.
   - Si el banner superior parpadea en ROJO («ALERTA: MASTER SATURANDO»),
     reducir de inmediato el potenciómetro de graves o atenuar el canal
     entrante -2 a -3 dB.
3. **Fase de auditoría (post-sesión):**
   - Inspeccionar el contador «Clips detectados». Un valor 0 certifica una
     sesión con rango dinámico limpio, sin compresión residual de Windows y sin
     distorsión armónica en los amplificadores.

## 7. Limitaciones conocidas

1. **Punto de captura post-mezcla.** El loopback WASAPI entrega la señal tal
   como sale del motor de audio (posterior a mezcla, APOs y limitador): es
   exactamente lo que llegará al DAC, que es lo que interesa proteger. No
   obstante, si Equalizer APO aplica *preamplificación negativa*, la huella del
   limitador puede quedar atenuada por debajo de -0.30 dBFS y la baliza no se
   disparará aunque el *pumping* sea audible. **Recomendación operativa:**
   preamp de Equalizer APO en 0 dB durante la sesión y corrección tonal solo
   con filtros de corte.
2. **Rutas ASIO.** Incompatibles por arquitectura (ver §3.1).
3. **Detección por techo, no por acusación.** La herramienta infiere la acción
   de CAudioLimiter desde el nivel del techo; se planifica un detector
   analítico por *crest factor* (techo plano sostenido) como mejora futura.

## 8. Trabajo futuro

- ~~Acción correctiva automatizada~~ **Implementada en v1.2.0 (§4.6)** mediante
  atajo de teclado nativo. Extensión prevista: salida MIDI nativa opcional
  (`mido`/`rtmidi`) para cabinas con controlador hardware.
- Detector analítico de aplastamiento por *crest factor* (techo plano
  sostenido) como segunda firma de la actuación del limitador.
- Registro CSV de eventos con marca temporal para auditoría post-sesión.
- Empaquetado `.exe` (PyInstaller) para cabinas sin Python instalado
  (dependencia `dev` ya declarada en `pyproject.toml`).

---

## Anexo A. Matriz de auditoría de código (v1.0.0 → v1.1.0)

| ID | Severidad | Hallazgo | Resolución |
| :--- | :--- | :--- | :--- |
| F-01 | **Crítica** | Hilo de captura moría en silencio ante excepciones; GUI congelada en «Conectando…» | Bucle supervisor con reintento *backoff* 1→5 s y error visible en barra de estado |
| F-02 | **Crítica** | Detección de clip sin histéresis: falsos eventos por micro-fluctuación y banner a ~23 Hz | Disparo -0.30 dBFS / rearme -1.0 dBFS (§4.4) |
| F-03 | **Alta** | Sin balística: pico instantáneo por bloque, falsos negativos visuales; IEC 60268-10 citada pero no implementada | Ataque instantáneo, caída 12 dB/s, peak-hold 1 s (§4.2) |
| F-04 | **Alta** | Ticks de regla por índices fijos: `-30` en -28.8 dB, `-18` en -19.2 dB (error 1.2 dB) | Función zonal compartida `db_to_slot()`; ticks exactos (§4.3) |
| F-05 | **Media** | Nombre de dispositivo capturado pero nunca mostrado | Barra de estado con endpoint, frecuencia y versión |
| F-06 | **Media** | Fórmula de suma incoherente (+3 dB) usada para un razonamiento coherente (+6 dB) | Ambas fórmulas corregidas y delimitadas (§2.1) |
| F-07 | **Media** | Guardia logarítmica `1e-5` (≈ -100 dB) incoherente con el piso de escala (-60 dB) | `amp_to_db()` con piso unificado |
| F-08 | **Media** | `sys.exit()` dentro del callback de Tk; stream MediaFoundation liberado por el GC | Cierre ordenado con señalización + `join` (§4.5) |
| F-09 | **Menor** | Documento prometía baliza intermitente; el código pintaba rojo fijo | Parpadeo 4 Hz durante la retención (§4.5) |
| F-10 | **Menor** | GUI borrosa en escalados 125/150 % de Windows | DPI Awareness con *fallback* (§4.5) |
| F-11 | **Nota** | Limitación no declarada: captura post-APO + preamp negativo enmascara al limitador | Documentada en §7 con recomendación operativa |

---

## Anexo B. Verificación

La lógica crítica del instrumento queda verificada por la suite
`tests/test_core.py` (**20 pruebas, sin hardware de audio**), ejecutable en
CI (`.github/workflows/ci.yml`, matriz Windows/Linux × Python 3.10–3.13):

| Bloque | Alcance |
| :--- | :--- |
| Calibración | `CLIP_AMP = -0.30 dBFS`, `REARM_AMP = -1.0 dBFS`, 36 LEDs en 3 zonas contiguas |
| Escala | Ticks exactos (−30→12.5, −18→17.5, −6→26.67), *clamp* fuera de rango, fronteras de zona |
| Histéresis F-02 | Clip sostenido = 1 evento; rearme solo bajo −1.0 dBFS |
| Balística F-03 | Ataque instantáneo, caída 12 dB/s, peak-hold 1 000 ms |
| Fase 2 | Atajos válidos e inválidos, modificadores múltiples |
| Física §2.1 | Suma coherente +6.02 dB · incoherente +3.01 dB |

*Documento generado y corregido en el marco de la auditoría técnica
AUD-VDJ-MON-01. Licencia MIT.*
