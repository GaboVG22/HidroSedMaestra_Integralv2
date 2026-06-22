# HidroSed Maestra Integral v3.0 · Dios Supremo

Plataforma Streamlit para hidrología, hidráulica 1D, sedimentos, socavación, DEM OpenTopography, curvas de nivel, secciones, rugosidad, granulometría KMZ, QA e incertidumbre.

## Main file path

```text
app.py
```

## Mejoras principales v3.0

1. Plataforma visual superior con panel tipo centro de control hidráulico.
2. Nuevo módulo de rugosidad avanzada: ingreso manual, tabla Manning, Cowan y Strickler/granulometría.
3. Rugosidad diferenciable por margen izquierda, cauce principal y margen derecha.
4. Sección trapezoidal estimada cuando no existan secciones suficientes desde DEM/topografía.
5. Secciones trapezoidales por tramo con ancho de fondo, profundidad, taludes, pendiente y cota inicial.
6. Capacidad hidráulica preliminar de secciones trapezoidales con tirante normal, crítico, velocidad y Froude.
7. Granulometría georreferenciada con tabla CSV/XLSX y KMZ/KML de muestras.
8. Validación granulométrica: orden D50/D84/D90, unidades, positividad y confianza.
9. Interpolación longitudinal de D50, D84, D90 y D95 por PK y asignación a cada sección.
10. Transferencia hidrológica dual área-altitud-distancia.
11. Semáforo maestro de confianza por bloque técnico.
12. Conserva descarga DEM OpenTopography normal o por teselas y mosaico interno.
13. Conserva delimitación de cuenca, curvas por teselas, secciones reales, hidrología, hidráulica conectada y 3D.
14. Agrega trazabilidad técnica para rugosidad y granulometría.
15. Agrega reporte interno de 10 corridas de verificación.

## Nuevos módulos

```text
modules/roughness_engine.py
modules/synthetic_trapezoid_sections.py
modules/granulometry_kmz.py
modules/hydrologic_transfer_dual.py
modules/supreme_dashboard.py
```

## Corridas internas

Se ejecutó una suite interna con 10 ciclos x 10 pruebas = 100 verificaciones OK.

Archivo de reporte:

```text
outputs/reporte_10_corridas_supremo.csv
```

## Limitaciones honestas

- No se probó descarga real OpenTopography desde esta sesión porque requiere API Key activa y ejecución con internet en Streamlit Cloud.
- La sección trapezoidal es un modo estimativo/preliminar y no reemplaza levantamiento topográfico.
- El motor hidráulico es 1D permanente tipo HEC-RAS simplificado/mejorado, útil para análisis técnico preliminar; no reemplaza una modelación oficial calibrada cuando existan singularidades, puentes, alcantarillas, flujo no permanente o condiciones 2D.
- La rugosidad estimada por Cowan/tabla/Strickler debe verificarse en terreno cuando el proyecto pase a diseño definitivo.


## Hotfix DEM

Corrección aplicada:
- Se agregó la importación faltante:
  `download_dem_normal_or_tiled` y `recommended_tiling`
  desde `modules/opentopo_tiled_download.py`.

Este hotfix corrige el error:
`NameError: name 'recommended_tiling' is not defined`.


## Hotfix Topografía Opcional

Corrección aplicada:
- Las curvas de nivel de apoyo topográfico quedan estrictamente opcionales.
- Si no se cargan, el proceso continúa con DEM.
- Si se cargan pero fallan, el proceso continúa con DEM.
- Si no contienen cotas válidas, el proceso continúa con DEM.
- Durante la generación de secciones, cualquier error del apoyo topográfico cae a modo DEM sin detener el flujo.


## Hotfix Curvas por Teselas

Corrección aplicada:
- Se reemplazó `cs.collections` por `cs.allsegs` en `modules/tiled_contours.py`.
- Corrige el error: `'QuadContourSet' object has no attribute 'collections'`.
- El modo por teselas vuelve a generar curvas KMZ/KML unificadas.


## Hotfix Cloud Safe para curvas

Corrección aplicada:
- `runtime.txt` con Python 3.11 para Streamlit Cloud.
- Dependencias geoespaciales acotadas.
- Curvas por teselas sin crear mallas X/Y grandes.
- Downsampling automático por tesela para evitar caída por memoria.
- `cs.allsegs` compatible con Matplotlib actual.
- Metadata por tesela para revisar factor de reducción, niveles y placemarks.


## v3.1 - Verificación cuenca + curvas de nivel

Se incorpora salida equivalente a la app de referencia cuencadem0:

- La cuenca se mantiene delimitada por el motor D8/Priority-Flood.
- Las curvas de nivel se pueden recortar al polígono de cuenca.
- Se genera un solo KMZ/KML con cuenca + curvas de nivel.
- Se agrega vista previa tipo EPSG:4326 con cuenca y curvas.
- Botón: `Descargar KMZ cuenca + curvas de nivel`.


## v3.2 - Curvas simples y límite 200.000 km²

Cambios:
- Se reemplazan ajustes difíciles de curvas por un modo simple:
  - Prueba rápida
  - Cuenca pequeña o media
  - Cuenca grande
  - Cuenca muy grande hasta 200.000 km²
- Se agrega selector de nivel de detalle:
  - Rápido y liviano
  - Equilibrado recomendado
  - Alto detalle
  - Máximo detalle pesado
- Los parámetros avanzados quedan ocultos en un expander.
- El límite QA de cuenca cambia de 10.000 km² a 200.000 km².
- La descarga DEM y curvas por teselas consideran cuencas hasta 200.000 km².


## v3.3 - Distancia entre curvas siempre visible

Cambios:
- Se elimina el término confuso "Límite QA superficie cuenca".
- Se reemplaza por "Tamaño máximo esperado de la cuenca [km²] (solo aviso)".
- La distancia entre curvas queda siempre visible y seleccionable.
- Se mantiene mínimo 1 m.
- Los ajustes técnicos quedan en "Ajustes avanzados de curvas".


## v3.4 FastLoad Streamlit Cloud

Objetivo: que la app abra rápido en Streamlit Cloud.

Cambios:
- `app.py` es ahora un launcher rápido.
- La plataforma completa queda como `app_full.py`.
- Los módulos pesados se importan solo cuando se presionan botones de cálculo.
- Se elimina `packages.txt` para evitar instalación apt lenta de GDAL.
- Se usa `runtime.txt` con Python 3.11.
- Se elimina dependencia obligatoria de `scikit-image`; la cuenca se vectoriza con `rasterio.features`.
- `requirements.txt` queda más liviano.
- Main file path sigue siendo `app.py`.

Modo recomendado:
1. Abrir app en modo `Carga rápida DEM/Cuenca/Curvas`.
2. Probar DEM, cuenca y curvas.
3. Cargar `Plataforma completa avanzada` solo cuando se necesiten todos los módulos hidráulicos/hidrológicos.
