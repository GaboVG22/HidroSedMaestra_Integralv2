
from __future__ import annotations

import io
import math
import re
import time
import zipfile
from pathlib import Path
from urllib.parse import urlencode

import requests
import streamlit as st

st.set_page_config(
    page_title="HidroSed FastLoad",
    page_icon="🌊",
    layout="wide",
    initial_sidebar_state="expanded",
)

OUT = Path("outputs")
OUT.mkdir(exist_ok=True)
if "project_id" not in st.session_state:
    st.session_state["project_id"] = str(int(time.time()))
PROJECT = OUT / st.session_state["project_id"]
PROJECT.mkdir(exist_ok=True)

BASE_URL = "https://portal.opentopography.org/API/globaldem"

CSS = """
<style>
.block-container {padding-top: 1.5rem; max-width: 1500px;}
.hs-hero {background: linear-gradient(135deg,#0b5cad,#00a0b0); color:white; padding:1.2rem 1.4rem; border-radius:18px; margin-bottom:1rem;}
.hs-hero h1 {margin:0; font-size:2rem;}
.hs-hero p {margin:.4rem 0 0 0; opacity:.95;}
.hs-card {border:1px solid #dbe5ef; border-radius:16px; padding:1rem; background:#ffffff;}
.hs-ok {background:#e8f8ee; border-left:5px solid #1f9d55; padding:.8rem; border-radius:10px;}
.hs-warn {background:#fff7e6; border-left:5px solid #f59e0b; padding:.8rem; border-radius:10px;}
.hs-small {font-size:.9rem; color:#4b5563;}
</style>
"""
st.markdown(CSS, unsafe_allow_html=True)


def save_bytes(name: str, data: bytes) -> Path:
    path = PROJECT / name
    path.write_bytes(data)
    return path


def has(key: str) -> bool:
    return key in st.session_state and st.session_state[key] is not None


def extract_kml_text(uploaded_file):
    data = uploaded_file.read()
    name = uploaded_file.name.lower()
    if name.endswith(".kmz"):
        with zipfile.ZipFile(io.BytesIO(data), "r") as z:
            names = [n for n in z.namelist() if n.lower().endswith(".kml")]
            if not names:
                raise ValueError("El KMZ no contiene KML.")
            return z.read(names[0]).decode("utf-8", errors="ignore")
    if name.endswith(".kml"):
        return data.decode("utf-8", errors="ignore")
    raise ValueError("Debe cargar KMZ o KML.")


def parse_first_point(kml_text: str):
    coords = re.findall(r"<coordinates[^>]*>(.*?)</coordinates>", kml_text, flags=re.I | re.S)
    if not coords:
        raise ValueError("No se encontraron coordenadas.")
    name_match = re.search(r"<name[^>]*>(.*?)</name>", kml_text, flags=re.I | re.S)
    name = re.sub("<.*?>", "", name_match.group(1)).strip() if name_match else "Punto de control"
    for block in coords:
        for token in re.split(r"\s+", block.strip()):
            vals = token.split(",")
            if len(vals) >= 2:
                try:
                    lon = float(vals[0])
                    lat = float(vals[1])
                    if -90 <= lat <= 90 and -180 <= lon <= 180:
                        return {"name": name, "lat": lat, "lon": lon}
                except Exception:
                    pass
    raise ValueError("No se pudo leer una coordenada lon,lat válida.")


def parse_first_linestring(kml_text: str):
    m = re.search(r"<LineString[^>]*>.*?<coordinates[^>]*>(.*?)</coordinates>.*?</LineString>", kml_text, flags=re.I | re.S)
    if not m:
        raise ValueError("No se encontró LineString para eje de cauce.")
    coords = []
    for token in re.split(r"\s+", m.group(1).strip()):
        vals = token.split(",")
        if len(vals) >= 2:
            try:
                coords.append((float(vals[0]), float(vals[1])))
            except Exception:
                pass
    if len(coords) < 2:
        raise ValueError("El eje no tiene suficientes puntos.")
    return coords


def bbox_from_margin(lat, lon, margin_value, margin_unit):
    if margin_unit == "km":
        dlat = margin_value / 111.32
        dlon = margin_value / (111.32 * max(math.cos(math.radians(lat)), 0.01))
    else:
        dlat = margin_value
        dlon = margin_value
    return {
        "south": round(lat - dlat, 8),
        "north": round(lat + dlat, 8),
        "west": round(lon - dlon, 8),
        "east": round(lon + dlon, 8),
    }


def bbox_area_km2(bbox):
    radius_km = 6371.0088
    s = math.radians(bbox["south"])
    n = math.radians(bbox["north"])
    w = math.radians(bbox["west"])
    e = math.radians(bbox["east"])
    return radius_km**2 * abs(math.sin(n) - math.sin(s)) * abs(e - w)


def build_url(dem_type, bbox, api_key_hidden="API_KEY_OCULTA"):
    params = {
        "demtype": dem_type,
        "south": bbox["south"],
        "north": bbox["north"],
        "west": bbox["west"],
        "east": bbox["east"],
        "outputFormat": "GTiff",
        "API_Key": api_key_hidden,
    }
    return f"{BASE_URL}?{urlencode(params)}"


def download_dem_normal(dem_type, bbox, api_key):
    if not api_key:
        raise ValueError("Ingresa tu API Key OpenTopography.")
    params = {
        "demtype": dem_type,
        "south": bbox["south"],
        "north": bbox["north"],
        "west": bbox["west"],
        "east": bbox["east"],
        "outputFormat": "GTiff",
        "API_Key": api_key.strip(),
    }
    r = requests.get(BASE_URL, params=params, timeout=(15, 420))
    if r.status_code >= 400:
        raise RuntimeError(f"OpenTopography respondió HTTP {r.status_code}: {r.text[:600]}")
    data = r.content
    if not (data.startswith(b"II*\x00") or data.startswith(b"MM\x00*")):
        txt = data[:800].decode("utf-8", errors="ignore")
        raise RuntimeError("La respuesta no parece GeoTIFF. Respuesta inicial:\n" + txt)
    return data


def split_bbox(bbox, rows, cols):
    out = []
    for i in range(rows):
        south = bbox["south"] + (bbox["north"] - bbox["south"]) * i / rows
        north = bbox["south"] + (bbox["north"] - bbox["south"]) * (i + 1) / rows
        for j in range(cols):
            west = bbox["west"] + (bbox["east"] - bbox["west"]) * j / cols
            east = bbox["west"] + (bbox["east"] - bbox["west"]) * (j + 1) / cols
            out.append({"south": south, "north": north, "west": west, "east": east, "tile": f"T{i+1:02d}_{j+1:02d}"})
    return out


def download_dem_tiled(dem_type, bbox, api_key, rows, cols, progress=None, status=None):
    import tempfile
    from pathlib import Path
    import rasterio
    from rasterio.merge import merge

    tmp = Path(tempfile.mkdtemp(prefix="hidrosed_dem_tiles_"))
    paths = []
    tiles = split_bbox(bbox, int(rows), int(cols))
    for idx, tb in enumerate(tiles, start=1):
        if status:
            status.info(f"Descargando DEM parcial {idx}/{len(tiles)} · {tb['tile']}")
        bb = {k: tb[k] for k in ["south", "north", "west", "east"]}
        data = download_dem_normal(dem_type, bb, api_key)
        fp = tmp / f"{tb['tile']}.tif"
        fp.write_bytes(data)
        paths.append(fp)
        if progress:
            progress.progress(min(0.8, idx / len(tiles) * 0.8))

    if status:
        status.info("Uniendo DEM parciales...")
    srcs = [rasterio.open(p) for p in paths]
    try:
        mosaic, transform = merge(srcs)
        meta = srcs[0].meta.copy()
        meta.update({
            "driver": "GTiff",
            "height": mosaic.shape[1],
            "width": mosaic.shape[2],
            "transform": transform,
            "compress": "deflate",
            "predictor": 2,
        })
        with rasterio.io.MemoryFile() as mem:
            with mem.open(**meta) as dst:
                dst.write(mosaic)
            return mem.read()
    finally:
        for s in srcs:
            s.close()


def recommended_dem_tiles(area_km2):
    if area_km2 <= 3000:
        return 1, 1
    if area_km2 <= 10000:
        return 2, 2
    if area_km2 <= 50000:
        return 3, 3
    if area_km2 <= 200000:
        return 5, 5
    return 6, 6


st.markdown(
    """
<div class="hs-hero">
<h1>🌊 HidroSed FastLoad</h1>
<p>Versión de carga rápida para Streamlit Cloud: entrada KMZ, DEM OpenTopography, cuenca, curvas de nivel y KMZ cuenca + curvas.</p>
</div>
""",
    unsafe_allow_html=True,
)

with st.sidebar:
    st.header("Modo de trabajo")
    modo_app = st.radio(
        "Selecciona",
        ["Carga rápida DEM/Cuenca/Curvas", "Plataforma completa avanzada"],
        index=0,
        help="La carga rápida inicia con pocos módulos. La plataforma completa carga toda la aplicación avanzada.",
    )

if modo_app == "Plataforma completa avanzada":
    st.warning("Cargando plataforma completa. Esta opción puede tardar más porque importa todos los módulos hidráulicos e hidrológicos.")
    code = Path("app_full.py").read_text(encoding="utf-8")
    exec(compile(code, "app_full.py", "exec"), globals())
    st.stop()

tabs = st.tabs(["1 · Entrada", "2 · DEM", "3 · Cuenca", "4 · Curvas", "5 · Cuenca + curvas", "6 · Ayuda"])

with tabs[0]:
    st.header("1 · Entrada geométrica")
    c1, c2 = st.columns(2)
    with c1:
        point_file = st.file_uploader("KMZ/KML con punto de control", type=["kmz", "kml"])
        if point_file and st.button("Leer punto de control"):
            try:
                cp = parse_first_point(extract_kml_text(point_file))
                st.session_state["control_point"] = cp
                st.success(f"Punto leído: {cp['name']} · lat {cp['lat']:.8f}, lon {cp['lon']:.8f}")
            except Exception as exc:
                st.error(str(exc))
    with c2:
        axis_file = st.file_uploader("KMZ/KML eje de cauce opcional", type=["kmz", "kml"])
        if axis_file and st.button("Leer eje"):
            try:
                coords = parse_first_linestring(extract_kml_text(axis_file))
                st.session_state["axis_line"] = coords
                st.success(f"Eje leído: {len(coords)} puntos.")
            except Exception as exc:
                st.warning(f"No se pudo leer eje. Puedes continuar sin eje. Detalle: {exc}")

    if has("control_point"):
        st.subheader("Punto activo")
        st.json(st.session_state["control_point"])

with tabs[1]:
    st.header("2 · DEM OpenTopography")
    if not has("control_point"):
        st.warning("Primero lee el punto de control.")
    else:
        cp = st.session_state["control_point"]
        c1, c2, c3 = st.columns(3)
        with c1:
            api_key = st.text_input("API Key OpenTopography", type="password")
            dem_type = st.selectbox("DEM", ["COP30", "NASADEM", "SRTMGL1", "SRTMGL3"], index=0)
        with c2:
            margin_unit = st.radio("Unidad margen", ["km", "grados"], horizontal=True)
            margin = st.number_input("Margen desde punto", min_value=0.001, value=40.0 if margin_unit == "km" else 0.40, step=5.0 if margin_unit == "km" else 0.05)
        with c3:
            download_mode = st.selectbox("Descarga DEM", ["Auto", "Normal", "Por partes"], index=0)

        bbox = bbox_from_margin(cp["lat"], cp["lon"], margin, margin_unit)
        area = bbox_area_km2(bbox)
        st.session_state["bbox_area_km2"] = float(area)
        rows_rec, cols_rec = recommended_dem_tiles(area)
        st.metric("Área bbox aproximada", f"{area:,.0f} km²".replace(",", "."))

        d1, d2 = st.columns(2)
        with d1:
            rows = st.selectbox("Partes verticales DEM", [1, 2, 3, 4, 5, 6], index=[1,2,3,4,5,6].index(rows_rec))
        with d2:
            cols = st.selectbox("Partes horizontales DEM", [1, 2, 3, 4, 5, 6], index=[1,2,3,4,5,6].index(cols_rec))

        st.code(build_url(dem_type, bbox), language="text")
        st.json(bbox)

        if st.button("Descargar DEM", type="primary"):
            try:
                progress = st.progress(0)
                status = st.empty()
                mode_tiled = download_mode == "Por partes" or (download_mode == "Auto" and area > 3000)
                if mode_tiled:
                    dem_bytes = download_dem_tiled(dem_type, bbox, api_key, rows, cols, progress=progress, status=status)
                else:
                    status.info("Descargando DEM en una solicitud...")
                    dem_bytes = download_dem_normal(dem_type, bbox, api_key)
                progress.progress(1.0)
                dem_path = save_bytes(f"dem_{dem_type}.tif", dem_bytes)
                st.session_state["dem_bytes"] = dem_bytes
                st.session_state["dem_path"] = str(dem_path)
                st.session_state["dem_bbox"] = bbox
                st.success(f"DEM listo: {len(dem_bytes)/(1024*1024):.2f} MB")
            except Exception as exc:
                st.error(str(exc))

        if has("dem_bytes"):
            st.download_button("Descargar DEM GeoTIFF", st.session_state["dem_bytes"], file_name="dem_hidrosed.tif", mime="image/tiff")

with tabs[2]:
    st.header("3 · Delimitar cuenca")
    if not has("dem_path") or not has("control_point"):
        st.warning("Necesitas DEM y punto de control.")
    else:
        st.caption("Modo simple. Los parámetros avanzados están ocultos.")
        c0, c1 = st.columns(2)
        with c0:
            tam = st.selectbox("Tamaño esperado", ["Prueba/pequeña", "Media", "Grande", "Muy grande hasta 200.000 km²"], index=1)
        presets = {
            "Prueba/pequeña": (1000, 2_500_000, 50),
            "Media": (2500, 6_000_000, 80),
            "Grande": (5000, 10_000_000, 150),
            "Muy grande hasta 200.000 km²": (10000, 20_000_000, 300),
        }
        snap, max_cells, simp = presets[tam]
        with c1:
            expected_area = st.number_input("Tamaño máximo esperado [km²] (solo aviso)", min_value=1.0, value=200000.0, step=1000.0)

        with st.expander("Ajustes avanzados de cuenca", expanded=False):
            snap = st.selectbox("Radio de búsqueda del cauce [m]", [100, 250, 500, 1000, 2500, 5000, 10000, 20000], index=[100,250,500,1000,2500,5000,10000,20000].index(snap) if snap in [100,250,500,1000,2500,5000,10000,20000] else 4)
            max_cells = st.selectbox("Capacidad de procesamiento DEM", [500_000, 1_000_000, 2_500_000, 6_000_000, 10_000_000, 20_000_000], index=[500_000,1_000_000,2_500_000,6_000_000,10_000_000,20_000_000].index(max_cells) if max_cells in [500_000,1_000_000,2_500_000,6_000_000,10_000_000,20_000_000] else 3)
            simp = st.selectbox("Suavizado borde de cuenca [m]", [0, 30, 50, 80, 120, 150, 200, 300, 500], index=[0,30,50,80,120,150,200,300,500].index(simp) if simp in [0,30,50,80,120,150,200,300,500] else 3)

        if st.button("Delimitar cuenca", type="primary"):
            try:
                with st.spinner("Delimitando cuenca..."):
                    from modules.watershed_morphometry import delineate_basin, metrics_dataframe
                    cp = st.session_state["control_point"]
                    res = delineate_basin(
                        st.session_state["dem_path"],
                        outlet_lon=float(cp["lon"]),
                        outlet_lat=float(cp["lat"]),
                        snap_radius_m=float(snap),
                        max_cells=int(max_cells),
                        simplify_m=float(simp),
                    )
                st.session_state["basin_kmz"] = res.kmz_bytes
                st.session_state["basin_kml"] = res.kml_bytes
                st.session_state["basin_preview"] = res.preview_png
                st.session_state["basin_metrics"] = res.metrics
                st.session_state["basin_metrics_df"] = metrics_dataframe(res.metrics)
                save_bytes("cuenca_delimitada.kmz", res.kmz_bytes)
                save_bytes("cuenca_delimitada.kml", res.kml_bytes)
                st.success("Cuenca delimitada.")
            except Exception as exc:
                st.error(str(exc))

        if has("basin_metrics"):
            m = st.session_state["basin_metrics"]
            k1, k2, k3, k4 = st.columns(4)
            k1.metric("Área", f"{m.get('area_km2',0):,.2f} km²".replace(",", "."))
            k2.metric("Perímetro", f"{m.get('perimetro_km',0):,.2f} km".replace(",", "."))
            k3.metric("Kc", f"{m.get('coef_compacidad_kc',0):.3f}")
            k4.metric("Factor forma", f"{m.get('factor_forma',0):.3f}")
            if float(m.get("area_km2", 0)) > expected_area:
                st.warning("La cuenca delimitada supera el tamaño esperado. Revisa punto de salida, DEM y margen.")
            if m.get("advertencias"):
                st.warning("Advertencias:")
                for a in m["advertencias"]:
                    st.write(f"- {a}")
            if has("basin_preview"):
                st.image(st.session_state["basin_preview"], use_container_width=True)
            st.download_button("Descargar cuenca KMZ", st.session_state["basin_kmz"], file_name="cuenca_delimitada.kmz", mime="application/vnd.google-earth.kmz")

with tabs[3]:
    st.header("4 · Curvas de nivel")
    if not has("dem_path"):
        st.warning("Primero descarga el DEM.")
    else:
        bbox_area_ref = float(st.session_state.get("bbox_area_km2", 0) or 0)
        c1, c2 = st.columns(2)
        with c1:
            interval = st.selectbox("Distancia entre curvas de nivel [m]", [1, 2, 5, 10, 20, 25, 50, 100, 200], index=5)
        with c2:
            tam_curvas = st.selectbox("Tamaño de trabajo", ["Prueba rápida", "Cuenca pequeña/media", "Cuenca grande", "Cuenca muy grande hasta 200.000 km²"], index=1 if bbox_area_ref < 10000 else 2 if bbox_area_ref < 50000 else 3)

        if interval == 1:
            st.warning("1 m es el máximo detalle y puede demorar bastante. Para prueba inicial usa 25 m, 50 m o 100 m.")
        elif interval <= 5:
            st.info("Detalle alto.")
        else:
            st.success("Configuración liviana o equilibrada.")

        preset_tiles = {
            "Prueba rápida": (3, 3, 5000),
            "Cuenca pequeña/media": (5, 5, 10000),
            "Cuenca grande": (8, 8, 20000),
            "Cuenca muy grande hasta 200.000 km²": (12, 12, 30000),
        }
        pr, pc, plevels = preset_tiles[tam_curvas]

        with st.expander("Ajustes avanzados de curvas", expanded=False):
            contour_mode = st.selectbox("Forma de procesamiento", ["Automático", "Normal", "Por teselas y unificado"], index=2)
            tile_rows = st.selectbox("Partes verticales", [2,3,4,5,6,8,10,12,16], index=[2,3,4,5,6,8,10,12,16].index(pr) if pr in [2,3,4,5,6,8,10,12,16] else 3)
            tile_cols = st.selectbox("Partes horizontales", [2,3,4,5,6,8,10,12,16], index=[2,3,4,5,6,8,10,12,16].index(pc) if pc in [2,3,4,5,6,8,10,12,16] else 3)
            max_levels = st.selectbox("Máximo de cotas", [1000,3000,5000,10000,20000,30000], index=[1000,3000,5000,10000,20000,30000].index(plevels) if plevels in [1000,3000,5000,10000,20000,30000] else 3)
            max_cells = st.selectbox("Detalle modo normal", [1_000_000,2_500_000,4_000_000,6_000_000,10_000_000,20_000_000], index=3)

        use_tiled = contour_mode == "Por teselas y unificado" or (contour_mode == "Automático" and bbox_area_ref >= 2500)
        if st.button("Generar curvas KMZ/KML", type="primary"):
            try:
                with st.spinner("Generando curvas de nivel..."):
                    if use_tiled:
                        from modules.tiled_contours import generate_tiled_contours_from_dem
                        out = generate_tiled_contours_from_dem(
                            st.session_state["dem_path"],
                            interval_m=float(interval),
                            tile_rows=int(tile_rows),
                            tile_cols=int(tile_cols),
                            max_levels=int(max_levels),
                            index_interval_m=max(float(interval)*10.0, 10.0),
                        )
                    else:
                        from modules.dem_processing import generate_contours
                        out = generate_contours(
                            st.session_state["dem_path"],
                            interval_m=float(interval),
                            max_cells=int(max_cells),
                            max_levels=int(max_levels),
                        )
                st.session_state["contours_kmz"] = out.kmz_bytes
                st.session_state["contours_kml"] = out.kml_bytes
                st.session_state["contours_preview"] = out.preview_png
                st.session_state["contours_meta"] = out.metadata
                save_bytes("curvas_nivel_unificadas.kmz", out.kmz_bytes)
                save_bytes("curvas_nivel_unificadas.kml", out.kml_bytes)
                st.success("Curvas generadas.")
            except Exception as exc:
                st.error(str(exc))

        if has("contours_preview"):
            st.image(st.session_state["contours_preview"], use_container_width=True)
        if has("contours_meta"):
            st.json(st.session_state["contours_meta"])
        if has("contours_kmz"):
            st.download_button("Descargar curvas KMZ", st.session_state["contours_kmz"], file_name="curvas_nivel.kmz", mime="application/vnd.google-earth.kmz")

with tabs[4]:
    st.header("5 · Cuenca + curvas")
    if not has("basin_kml") or not has("contours_kml"):
        st.info("Primero delimita cuenca y genera curvas.")
    else:
        clip = st.checkbox("Recortar curvas al polígono de cuenca", value=True)
        if st.button("Generar KMZ cuenca + curvas", type="primary"):
            try:
                with st.spinner("Uniendo cuenca + curvas..."):
                    from modules.basin_contours_export import build_basin_contours_kmz
                    out = build_basin_contours_kmz(st.session_state["basin_kml"], st.session_state["contours_kml"], clip_to_basin=clip)
                st.session_state["basin_contours_kmz"] = out.kmz_bytes
                st.session_state["basin_contours_kml"] = out.kml_bytes
                st.session_state["basin_contours_preview"] = out.preview_png
                st.session_state["basin_contours_meta"] = out.metadata
                save_bytes("cuenca_curvas_nivel.kmz", out.kmz_bytes)
                st.success("KMZ cuenca + curvas listo.")
            except Exception as exc:
                st.error(str(exc))
        if has("basin_contours_preview"):
            st.image(st.session_state["basin_contours_preview"], use_container_width=True)
        if has("basin_contours_meta"):
            st.json(st.session_state["basin_contours_meta"])
        if has("basin_contours_kmz"):
            st.download_button("Descargar KMZ cuenca + curvas", st.session_state["basin_contours_kmz"], file_name="cuenca_curvas_nivel.kmz", mime="application/vnd.google-earth.kmz")

with tabs[5]:
    st.header("6 · Ayuda rápida")
    st.markdown(
        """
### Para cargar rápido en Streamlit Cloud

Esta versión abre primero una interfaz liviana. Los módulos pesados se importan solo cuando presionas botones de cálculo.

### Curvas de nivel

La distancia entre curvas siempre se elige manualmente:

- 1 m: máximo detalle, puede ser pesado.
- 10 a 25 m: detalle alto/equilibrado.
- 50 a 100 m: recomendado para cuencas grandes.
- 200 m: revisión rápida de cuencas muy grandes.

### Tamaño máximo esperado

Es solo un aviso. No detiene el cálculo.
"""
    )
