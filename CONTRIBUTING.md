# Contribuir a AUD-VDJ-MON-01

Gracias por tu interés. Este es un proyecto de cabina pequeño y deliberadamente
simple: **una sola dependencia de captura, cero estado persistente, cero
configuración oculta**. Mantengámoslo así.

## Cómo proponer cambios

1. Abre un *issue* describiendo el problema o la mejora (si es un fallo de
   captura, incluye: versión de Windows, modelo de tarjeta USB, modo de salida
   configurado en Virtual DJ y salida de `python aud_vdj_mon_01.py --list`).
2. Haz *fork*, crea una rama `fix/nombre-descriptivo` y abre el PR.

## Convenciones

- Código en inglés, comentarios y documentación en español.
- Estilo: PEP 8, anotaciones de tipo en la API pública, líneas ≤ 100.
- La GUI es Tkinter puro: no introducir Qt/DearPyGui sin discusión previa.
- Toda métrica nueva (LUFS, correlación de fase…) debe ser opcional y no
  romper la lectura periférica de los dos medidores principales.
- La precisión por debajo de -60 dBFS no es objetivo del proyecto.

## Pruebas de humo mínimas (manual, Windows)

```powershell
pip install -r requirements.txt
python aud_vdj_mon_01.py --list          # debe listar tu endpoint loopback
python aud_vdj_mon_01.py --device "USB"  # debe abrir la GUI con audio real
```

- Reproduce música y verifica que los picos verdes/amarillos responden.
- Sube el master de Virtual DJ hasta provocar clip: la baliza debe parpadear
  y el contador incrementar **una vez por excursión**, no por bloque.
- Desconecta la tarjeta USB con el monitor abierto: debe mostrar el error en
  la barra de estado y recuperarse solo al reconectar.

## Licencia

Al contribuir aceptas que tu aporte se distribuya bajo la licencia MIT del
repositorio.
