
from __future__ import annotations

import json
import time
from pathlib import Path

import pandas as pd
import streamlit as st
from shapely.geometry import LineString

from modules.kmz_utils import read_kml, parse_first_point, parse_lines, line_to_shapely_wgs84
from modules.opentopo_engine import bbox_from_margin, bbox_area_km2, build_url, download_dem
from modules.opentopo_tiled_download import download_dem_normal_or_tiled, recommended_tiling
from modules.dem_processing import generate_contours
from modules.tiled_contours import generate_tiled_contours_from_dem, split_bbox_km2_strategy
from modules.topography_support import read_kmz_kml_bytes, parse_topographic_contours, improve_section_points_with_topo
from modules.section_qaqc import select_and_fill_sections, section_report_summary
from modules.visual_3d_hydraulic import create_3d_profile_figure, figure_to_html_bytes
from modules.watershed_morphometry import delineate_basin, metrics_dataframe
from modules.axis_sections import generate_preliminary_axis, export_axis_kmz, generate_cross_sections, sections_excel_bytes
from modules.hydrology_methods import DEFAULT_T, rational_method, dga_ac_series, combine_design_flows, time_concentration_kirpich
from modules.sediment_scour import hydraulic_and_sediment
from modules.hydraulic_hecras_like import hecras_like_steady_profile, sediment_from_hecras_profile
from modules.cartographic_output import make_cartographic_sheet
from modules.roughness_engine import ROUGHNESS_TABLE, COWAN_FACTORS, suggested_roughness, compose_roughness_manual, cowan_n, table_n, roughness_confidence
from modules.synthetic_trapezoid_sections import generate_trapezoid_reach_sections, trapezoid_capacity_table
from modules.granulometry_kmz import read_kmz_or_kml_to_text, parse_granulometry_points, normalize_granulometry_table, validate_granulometry, assign_granulometry_to_sections
from modules.hydrologic_transfer_dual import transfer_flow_area_altitude_distance, rank_hydrometric_stations
from modules.supreme_dashboard import CSS, kpi_html, global_confidence_report
from modules.basin_contours_export import build_basin_contours_kmz

# set_page_config se define en app.py (launcher rápido)

st.markdown(CSS, unsafe_allow_html=True)

OUT = Path("outputs")
OUT.mkdir(exist_ok=True)

if "project_id" not in st.session_state:
    st.session_state["project_id"] = str(int(time.time()))
PROJECT = OUT / st.session_state["project_id"]
PROJECT.mkdir(parents=True, exist_ok=True)


def has(key: str) -> bool:
    v = st.session_state.get(key)
    if v is None:
        return False
    if hasattr(v, "empty"):
        return not v.empty
    if isinstance(v, (str, bytes, list, tuple, dict)):
        return len(v) > 0
    return True


def badge(key, label):
    if has(key):
        st.sidebar.success(f"✓ {label}")
    else:
        st.sidebar.warning(f"○ {label}")


def save_bytes(name: str, data: bytes) -> Path:
    path = PROJECT / name
    path.write_bytes(data)
    return path


def periods_from_text(txt: str):
    vals = set(DEFAULT_T)
    if txt.strip():
        for t in txt.replace(";", ",").split(","):
            try:
                vals.add(float(t.strip()))
            except Exception:
                pass
    return sorted(vals)


st.sidebar.title("HidroSed v3.3 Supremo")
st.sidebar.caption("Centro de control hidráulico-hidrológico · QA · 3D · trazabilidad")
for k, label in [
    ("control_point", "1 Punto control"),
    ("axis_line", "1 Eje cauce"),
    ("topo_support_df", "1 Curvas apoyo topo"),
    ("dem_path", "2 DEM"),
    ("basin_metrics", "3 Cuenca/morfometría"),
    ("contours_kmz", "4 Curvas"),
    ("sections_df", "4 Secciones"),
    ("hydrology_done", "5 Hidrología"),
    ("q_design", "6 Caudales"),
    ("hydraulic_profile_df", "8 Perfil tipo HEC-RAS"),
    ("sediment_df", "8 Socavación/sedimentos"),
    ("profile_3d_html", "8 Perfil 3D hidráulico"),
    ("cartographic_png", "9 Lámina cartográfica"),
]:
    badge(k, label)

st.markdown(
    """
<div class='hs-hero'>
  <h1>🌊 HidroSed Maestra Integral v3.3 Curvas Simples con Distancia Manual · Dios Supremo · Hotfix DEM · Topo Opcional</h1>
  <p>Plataforma hidráulica-hidrológica avanzada para cuencas y cauces: DEM OpenTopography, delimitación, curvas, secciones reales o trapezoidales, hidrología normativa, hidráulica 1D tipo HEC‑RAS mejorada, rugosidad avanzada, granulometría georreferenciada, sedimentos, socavación, QA, incertidumbre y visualización 3D.</p>
  <span class='hs-pill'>HEC-RAS 1D enhanced</span><span class='hs-pill'>Hidrología DGA/MC</span><span class='hs-pill'>Rugosidad Cowan/Strickler</span><span class='hs-pill'>Sección trapezoidal fallback</span><span class='hs-pill'>Granulometría KMZ</span>
</div>
""",
    unsafe_allow_html=True,
)

st.info(
    "Secuencia oficial: 1 Entrada → 2 DEM → 3 Cuenca/Morfometría → 4 Curvas/Eje → "
    "5 Secciones → 6 Hidrología → 7 Caudales → 8 Hidráulica/Sedimentos → 9 Exportación → 10 Modo Supremo QA/Rugosidad. "
    "Modo recomendado: cuencas hasta 200.000 km² con DEM COP30, descarga por partes y controles QA."
)

tabs = st.tabs([
    "1 · Entrada",
    "2 · DEM OpenTopo",
    "3 · Cuenca y morfometría",
    "4 · Curvas y eje",
    "5 · Secciones",
    "6 · Hidrología",
    "7 · Caudales",
    "8 · Socavación y sedimentos",
    "9 · Cartografía y exportar",
    "10 · Supremo QA/Rugosidad/Trapezoidal",
])

with tabs[0]:
    st.header("1 · Entrada geométrica")
    c1, c2 = st.columns(2)
    with c1:
        point_file = st.file_uploader("KMZ/KML con punto de control", type=["kmz", "kml"], key="point_file")
        if point_file and st.button("Leer punto de control"):
            try:
                kml = read_kml(point_file)
                cp = parse_first_point(kml)
                st.session_state["control_point"] = {"lat": cp.lat, "lon": cp.lon, "name": cp.name}
                st.success(f"Punto leído: {cp.name} · lat {cp.lat:.8f}, lon {cp.lon:.8f}")
            except Exception as exc:
                st.error(str(exc))
    with c2:
        axis_file = st.file_uploader("KMZ/KML eje de cauce opcional", type=["kmz", "kml"], key="axis_file")
        if axis_file and st.button("Leer eje de cauce"):
            try:
                kml = read_kml(axis_file)
                lines = parse_lines(kml)
                if not lines:
                    raise ValueError("No se encontró LineString válido para eje de cauce.")
                line = line_to_shapely_wgs84(lines[0])
                st.session_state["axis_line"] = list(line.coords)
                st.success(f"Eje leído: {lines[0].name} · puntos {len(st.session_state['axis_line'])}")
            except Exception as exc:
                st.error(str(exc))

    st.divider()
    st.subheader("Curvas de nivel de apoyo topográfico opcionales")
    st.caption("Este archivo es 100% opcional. Si no se carga, si falla la lectura o si no contiene cotas válidas, la app continúa usando solo el DEM.")
    topo_file = st.file_uploader(
        "KMZ/KML con curvas de nivel topográficas de apoyo",
        type=["kmz", "kml"],
        key="topo_support_file",
        help="Archivo opcional. Mejora cotas de secciones si las curvas contienen cota en nombre, ExtendedData o coordenada Z.",
    )

    if not topo_file and "topo_support_df" not in st.session_state:
        st.info("Sin curvas de apoyo topográfico: el proceso continuará normalmente con el DEM.")

    if topo_file and st.button("Leer curvas topográficas de apoyo"):
        try:
            topo_kml = read_kmz_kml_bytes(topo_file)
            topo_df = parse_topographic_contours(topo_kml)

            if topo_df is None or topo_df.empty:
                st.session_state.pop("topo_support_df", None)
                st.warning("El archivo fue leído, pero no se detectaron curvas útiles. Se continuará solo con DEM.")
            elif "z_m" not in topo_df.columns or topo_df["z_m"].notna().sum() == 0:
                st.session_state.pop("topo_support_df", None)
                st.warning("El archivo no contiene cotas reconocibles. Se continuará solo con DEM.")
            else:
                st.session_state["topo_support_df"] = topo_df
                st.success(f"Curvas de apoyo leídas: {topo_df['contour_id'].nunique()} curvas · {len(topo_df)} vértices · {topo_df['z_m'].notna().sum()} cotas válidas.")
        except Exception as exc:
            st.session_state.pop("topo_support_df", None)
            st.warning(f"No fue posible usar las curvas topográficas de apoyo. El proceso continuará solo con DEM. Detalle: {exc}")

    if has("topo_support_df"):
        topo_ok = st.session_state["topo_support_df"]
        st.caption("Muestra de curvas topográficas de apoyo cargadas")
        st.dataframe(topo_ok.head(100), use_container_width=True)
        if st.button("Quitar curvas de apoyo y continuar solo con DEM"):
            st.session_state.pop("topo_support_df", None)
            st.success("Curvas de apoyo removidas. La app continuará solo con DEM.")

    if has("control_point"):
        st.subheader("Punto de control activo")
        st.json(st.session_state["control_point"])
    if has("axis_line"):
        st.subheader("Eje de cauce activo")
        st.write(f"Puntos del eje: {len(st.session_state['axis_line'])}")

with tabs[1]:
    st.header("2 · Generar DEM desde OpenTopography")
    if not has("control_point"):
        st.warning("Primero ingresa el KMZ/KML con punto de control.")
    else:
        cp = st.session_state["control_point"]
        c1, c2, c3 = st.columns(3)
        with c1:
            api_key = st.text_input("API Key OpenTopography", type="password", key="api_key_manual")
            dem_type = st.selectbox("DEM", ["COP30", "NASADEM", "SRTMGL1", "SRTMGL3"], index=0)
        with c2:
            margin_unit = st.radio("Unidad margen", ["km", "grados"], horizontal=True)
            default_margin = 60.0 if margin_unit == "km" else 0.60
            margin = st.number_input(
                "Margen desde punto",
                min_value=0.001,
                value=default_margin,
                step=5.0 if margin_unit == "km" else 0.05,
                help="Para cuencas grandes ajuste hasta que la cuenca no toque el borde del DEM."
            )
        with c3:
            area_limit = st.number_input(
                "Límite técnico bbox [km²]",
                min_value=1.0,
                value=250000.0,
                step=10000.0,
                help="Para cuencas grandes o muy grandes se recomienda ajustar el margen hasta que la cuenca no toque el borde del DEM. Para cuencas de hasta 200.000 km² use descarga por partes."
            )

        bbox = bbox_from_margin(cp["lat"], cp["lon"], margin, margin_unit)
        area = bbox_area_km2(bbox)
        st.session_state["bbox_area_km2"] = float(area)
        st.metric("Área bbox aprox.", f"{area:,.1f} km²")

        rec = recommended_tiling(area)
        st.caption(f"Recomendación descarga DEM: {rec['mode']} · {rec['rows']} x {rec['cols']} teselas")

        if area < 10000:
            st.warning("El bbox es menor a 10.000 km². Sirve para pruebas o cuencas menores. Para cuencas grandes aumente el margen.")
        elif area > 250000:
            st.warning("El bbox supera 250.000 km². Puede ser pesado; usa descarga por partes y usa curvas de 100 m o 200 m para comenzar.")

        st.json(bbox)
        st.code(build_url(dem_type, bbox, "API_KEY_OCULTA"), language="text")

        st.subheader("Modo de descarga DEM")
        d1, d2, d3 = st.columns(3)
        with d1:
            download_mode = st.selectbox("Descarga DEM", ["Auto", "Normal", "Por partes"], index=0)
        with d2:
            tile_rows_dem = st.selectbox("Filas DEM", [1, 2, 3, 4, 5, 6, 8], index=[1,2,3,4,5,6,8].index(rec["rows"]) if rec["rows"] in [1,2,3,4,5,6,8] else 2)
        with d3:
            tile_cols_dem = st.selectbox("Columnas DEM", [1, 2, 3, 4, 5, 6, 8], index=[1,2,3,4,5,6,8].index(rec["cols"]) if rec["cols"] in [1,2,3,4,5,6,8] else 2)

        if area > area_limit:
            st.error("El bbox supera el límite técnico definido. Reduce margen o aumenta límite.")
        else:
            st.info("Con tu API Key, la app descargará el DEM directamente. Si el área es grande, use Auto o Por partes para descargar teselas y unirlas en un GeoTIFF único.")

        if st.button("Descargar DEM GeoTIFF", type="primary"):
            try:
                progress = st.progress(0.0)
                status = st.empty()

                def cb(msg, frac):
                    status.info(msg)
                    progress.progress(min(max(float(frac), 0.0), 1.0))

                result = download_dem_normal_or_tiled(
                    dem_type,
                    bbox,
                    api_key,
                    mode=download_mode,
                    rows=int(tile_rows_dem),
                    cols=int(tile_cols_dem),
                    progress_callback=cb,
                )
                dem_bytes = result.dem_bytes
                dem_path = save_bytes(f"dem_{dem_type}_unificado.tif", dem_bytes)
                st.session_state["dem_path"] = str(dem_path)
                st.session_state["dem_bytes"] = dem_bytes
                st.session_state["dem_bbox"] = bbox
                st.session_state["dem_download_meta"] = result.metadata
                progress.progress(1.0)
                status.success("DEM listo para delimitación, curvas y secciones.")
                st.success(f"DEM descargado/unificado: {len(dem_bytes)/(1024*1024):.2f} MB")
            except Exception as exc:
                st.error(str(exc))

        if has("dem_download_meta"):
            st.subheader("Metadata descarga DEM")
            st.json(st.session_state["dem_download_meta"])

        if has("dem_bytes"):
            st.download_button("Descargar DEM", st.session_state["dem_bytes"], file_name="dem_hidrosed_unificado.tif", mime="image/tiff")

with tabs[2]:
    st.header("3 · Delimitar cuenca y calcular parámetros morfológicos")
    if not has("dem_path") or not has("control_point"):
        st.warning("Necesitas DEM descargado y punto de control.")
    else:
        cp = st.session_state["control_point"]

        st.subheader("Modo simple de delimitación")
        tipo_cuenca = st.selectbox(
            "Tamaño esperado de la cuenca",
            [
                "Pequeña o prueba rápida (< 1.000 km²)",
                "Media (1.000 a 10.000 km²)",
                "Grande (10.000 a 50.000 km²)",
                "Muy grande (50.000 a 200.000 km²)",
                "Personalizado"
            ],
            index=1,
            help="Esto solo ajusta parámetros internos de procesamiento; no cambia la cuenca real."
        )

        presets_basin = {
            "Pequeña o prueba rápida (< 1.000 km²)": dict(snap=1000, cells=2_500_000, simp=50, limit=1_000.0),
            "Media (1.000 a 10.000 km²)": dict(snap=2500, cells=6_000_000, simp=80, limit=10_000.0),
            "Grande (10.000 a 50.000 km²)": dict(snap=5000, cells=10_000_000, simp=150, limit=50_000.0),
            "Muy grande (50.000 a 200.000 km²)": dict(snap=10000, cells=20_000_000, simp=300, limit=200_000.0),
            "Personalizado": dict(snap=2500, cells=6_000_000, simp=80, limit=200_000.0),
        }
        pb = presets_basin[tipo_cuenca]

        st.info(
            "Lectura simple: la app buscará el cauce más cercano al punto, delimitará la cuenca y avisará si toca el borde del DEM. "
            "Para cuencas grandes, lo importante es descargar un DEM bastante más grande que la cuenca esperada."
        )

        with st.expander("Ajustes avanzados de delimitación", expanded=False):
            c1, c2, c3 = st.columns(3)
            with c1:
                snap_radius = st.selectbox("Radio de búsqueda del cauce desde el punto [m]", [100, 250, 500, 1000, 1500, 2500, 5000, 10000, 20000], index=[100,250,500,1000,1500,2500,5000,10000,20000].index(pb["snap"]) if pb["snap"] in [100,250,500,1000,1500,2500,5000,10000,20000] else 5)
                st.caption("Más grande = la app puede mover más el punto para encontrar el cauce.")
            with c2:
                basin_max_cells = st.selectbox("Capacidad de procesamiento DEM", [500_000, 1_000_000, 2_500_000, 6_000_000, 10_000_000, 20_000_000], index=[500_000,1_000_000,2_500_000,6_000_000,10_000_000,20_000_000].index(pb["cells"]) if pb["cells"] in [500_000,1_000_000,2_500_000,6_000_000,10_000_000,20_000_000] else 3, format_func=lambda x: f"{x:,}".replace(",", "."))
                st.caption("Más alto = más detalle, pero más tiempo.")
            with c3:
                simplify_basin = st.selectbox("Suavizado del borde de cuenca [m]", [0, 30, 50, 80, 120, 150, 200, 300, 500], index=[0,30,50,80,120,150,200,300,500].index(pb["simp"]) if pb["simp"] in [0,30,50,80,120,150,200,300,500] else 3)
                basin_area_limit = st.number_input("Tamaño máximo esperado de la cuenca [km²] (solo aviso)", min_value=1.0, value=float(pb["limit"]), step=1000.0)
                st.caption("No detiene el cálculo. Solo sirve para advertir si la cuenca delimitada es mucho mayor que lo esperado.")

        if st.button("Delimitar cuenca desde DEM + punto de control", type="primary"):
            try:
                result = delineate_basin(
                    st.session_state["dem_path"],
                    outlet_lon=float(cp["lon"]),
                    outlet_lat=float(cp["lat"]),
                    snap_radius_m=float(snap_radius),
                    max_cells=int(basin_max_cells),
                    simplify_m=float(simplify_basin),
                )
                st.session_state["basin_kmz"] = result.kmz_bytes
                st.session_state["basin_kml"] = result.kml_bytes
                st.session_state["basin_preview"] = result.preview_png
                st.session_state["basin_metrics"] = result.metrics
                st.session_state["basin_metrics_df"] = metrics_dataframe(result.metrics)
                save_bytes("cuenca_delimitada.kmz", result.kmz_bytes)
                save_bytes("cuenca_delimitada.kml", result.kml_bytes)
                if result.preview_png:
                    save_bytes("preview_cuenca.png", result.preview_png)
                st.success("Cuenca delimitada y morfometría calculada.")
            except Exception as exc:
                st.error(str(exc))

        if has("basin_metrics"):
            m = st.session_state["basin_metrics"]
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Área", f"{m['area_km2']:.3f} km²")
            c2.metric("Perímetro", f"{m['perimetro_km']:.3f} km")
            c3.metric("Kc", f"{m['coef_compacidad_kc']:.3f}")
            c4.metric("Factor forma", f"{m['factor_forma']:.3f}")
            if float(m.get("area_km2", 0)) > 200000:
                st.warning("La cuenca delimitada supera 200.000 km². La app puede mostrar resultados preliminares, pero se recomienda dividir el análisis o aumentar la simplificación.")
            if m.get("advertencias"):
                st.warning("Advertencias QA:")
                for a in m["advertencias"]:
                    st.write(f"- {a}")
            else:
                st.success("QA cuenca: sin advertencias automáticas. Revisar igualmente en la vista previa/KMZ.")
            st.dataframe(st.session_state["basin_metrics_df"], use_container_width=True)
            if has("basin_preview"):
                st.image(st.session_state["basin_preview"], caption="Cuenca delimitada y acumulación de flujo", use_container_width=True)
            d1, d2 = st.columns(2)
            d1.download_button("Descargar cuenca KMZ", st.session_state["basin_kmz"], file_name="cuenca_delimitada.kmz", mime="application/vnd.google-earth.kmz")
            d2.download_button("Descargar cuenca KML", st.session_state["basin_kml"], file_name="cuenca_delimitada.kml", mime="application/vnd.google-earth.kml+xml")


with tabs[3]:
    st.header("4 · Curvas de nivel y eje de cauce")
    if not has("dem_path"):
        st.warning("Primero descarga el DEM.")
    else:
        st.subheader("Modo simple para curvas de nivel")
        bbox_area_ref = float(st.session_state.get("bbox_area_km2", 0) or 0)

        clase_curvas = st.selectbox(
            "Tamaño de trabajo",
            [
                "Prueba rápida",
                "Cuenca pequeña o media",
                "Cuenca grande",
                "Cuenca muy grande hasta 200.000 km²",
                "Personalizado"
            ],
            index=1 if bbox_area_ref < 10000 else 2 if bbox_area_ref < 50000 else 3,
            help="Elige una opción entendible. La app ajusta distancia entre curvas, teselas y peso del KMZ."
        )

        detalle_curvas = st.selectbox(
            "Nivel de detalle",
            [
                "Rápido y liviano",
                "Equilibrado recomendado",
                "Alto detalle",
                "Máximo detalle pesado"
            ],
            index=1,
            help="Más detalle genera más curvas, archivos más grandes y mayor tiempo de proceso."
        )

        # Presets simples.
        preset = {
            "Prueba rápida": {
                "Rápido y liviano": (50, "Por teselas y unificado", 3000, 3, 3, 4_000_000),
                "Equilibrado recomendado": (25, "Por teselas y unificado", 5000, 3, 3, 6_000_000),
                "Alto detalle": (10, "Por teselas y unificado", 10000, 4, 4, 10_000_000),
                "Máximo detalle pesado": (5, "Por teselas y unificado", 20000, 5, 5, 20_000_000),
            },
            "Cuenca pequeña o media": {
                "Rápido y liviano": (50, "Por teselas y unificado", 5000, 4, 4, 6_000_000),
                "Equilibrado recomendado": (25, "Por teselas y unificado", 10000, 5, 5, 10_000_000),
                "Alto detalle": (10, "Por teselas y unificado", 20000, 6, 6, 20_000_000),
                "Máximo detalle pesado": (5, "Por teselas y unificado", 30000, 8, 8, 20_000_000),
            },
            "Cuenca grande": {
                "Rápido y liviano": (100, "Por teselas y unificado", 5000, 5, 5, 6_000_000),
                "Equilibrado recomendado": (50, "Por teselas y unificado", 10000, 6, 6, 10_000_000),
                "Alto detalle": (25, "Por teselas y unificado", 20000, 8, 8, 20_000_000),
                "Máximo detalle pesado": (10, "Por teselas y unificado", 30000, 10, 10, 20_000_000),
            },
            "Cuenca muy grande hasta 200.000 km²": {
                "Rápido y liviano": (200, "Por teselas y unificado", 5000, 8, 8, 6_000_000),
                "Equilibrado recomendado": (100, "Por teselas y unificado", 10000, 10, 10, 10_000_000),
                "Alto detalle": (50, "Por teselas y unificado", 20000, 12, 12, 20_000_000),
                "Máximo detalle pesado": (25, "Por teselas y unificado", 30000, 16, 16, 20_000_000),
            },
            "Personalizado": {
                "Rápido y liviano": (50, "Automático", 5000, 5, 5, 6_000_000),
                "Equilibrado recomendado": (25, "Automático", 10000, 6, 6, 10_000_000),
                "Alto detalle": (10, "Automático", 20000, 8, 8, 20_000_000),
                "Máximo detalle pesado": (5, "Automático", 30000, 10, 10, 20_000_000),
            },
        }
        p_interval, p_mode, p_levels, p_rows, p_cols, p_cells = preset[clase_curvas][detalle_curvas]

        st.info(
            f"Recomendación inicial: {p_rows} x {p_cols} partes · modo {p_mode}. "
            "La distancia entre curvas la eliges manualmente abajo."
        )

        interval_values = [1, 2, 5, 10, 20, 25, 50, 100, 200]
        default_interval = p_interval if p_interval in interval_values else 50

        interval = st.selectbox(
            "Distancia entre curvas de nivel [m]",
            interval_values,
            index=interval_values.index(default_interval),
            help="Mínimo 1 m. Mientras menor sea la distancia, más curvas se generan y más pesado queda el KMZ."
        )

        if interval == 1:
            st.warning("1 m genera el máximo detalle, pero puede producir archivos muy pesados. Úsalo cuando el DEM y la cuenca ya estén verificados.")
        elif interval <= 5:
            st.info("Detalle alto. Adecuado para zonas pequeñas o cuando necesitas precisión topográfica.")
        elif interval <= 25:
            st.success("Detalle equilibrado para muchas cuencas de trabajo.")
        else:
            st.success("Modo liviano. Recomendado para cuencas grandes o muy grandes.")

        with st.expander("Ajustes avanzados de curvas", expanded=False):
            c1, c2, c3 = st.columns(3)
            with c1:
                contour_mode = st.selectbox("Forma de procesamiento", ["Automático", "Normal", "Por teselas y unificado"], index=["Automático","Normal","Por teselas y unificado"].index(p_mode) if p_mode in ["Automático","Normal","Por teselas y unificado"] else 0)
                st.caption("Por teselas = procesa por partes para cuencas grandes.")
            with c2:
                max_levels = st.selectbox("Máximo de cotas a generar", [1000, 3000, 5000, 10000, 20000, 30000], index=[1000,3000,5000,10000,20000,30000].index(p_levels) if p_levels in [1000,3000,5000,10000,20000,30000] else 3)
                st.caption("Controla el peso máximo del archivo.")
            with c3:
                max_cells = st.selectbox("Detalle máximo en modo normal", [1_000_000, 2_500_000, 4_000_000, 6_000_000, 10_000_000, 20_000_000], index=[1_000_000,2_500_000,4_000_000,6_000_000,10_000_000,20_000_000].index(p_cells) if p_cells in [1_000_000,2_500_000,4_000_000,6_000_000,10_000_000,20_000_000] else 3, format_func=lambda x: f"{x:,}".replace(",", "."))
                st.caption("Solo aplica si usas modo normal.")

            c4, c5 = st.columns(2)
            with c4:
                tile_rows = st.selectbox("Partes verticales", [2, 3, 4, 5, 6, 8, 10, 12, 16], index=[2,3,4,5,6,8,10,12,16].index(p_rows) if p_rows in [2,3,4,5,6,8,10,12,16] else 5)
            with c5:
                tile_cols = st.selectbox("Partes horizontales", [2, 3, 4, 5, 6, 8, 10, 12, 16], index=[2,3,4,5,6,8,10,12,16].index(p_cols) if p_cols in [2,3,4,5,6,8,10,12,16] else 5)

        use_tiled = contour_mode == "Por teselas y unificado" or (contour_mode == "Automático" and bbox_area_ref >= 2500)

        if use_tiled:
            st.success("Modo seguro para cuencas grandes: el DEM se procesa por partes y luego se une en un solo KMZ/KML.")
        else:
            st.info("Modo normal: recomendado solo para pruebas o DEM pequeños.")

        if st.button("Generar curvas KMZ/KML", type="primary"):
            try:
                if use_tiled:
                    out = generate_tiled_contours_from_dem(
                        st.session_state["dem_path"],
                        interval_m=float(interval),
                        tile_rows=int(tile_rows),
                        tile_cols=int(tile_cols),
                        max_levels=int(max_levels),
                        index_interval_m=max(float(interval) * 10.0, 10.0),
                    )
                else:
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
                if out.preview_png:
                    save_bytes("preview_curvas.png", out.preview_png)
                st.success("Curvas generadas correctamente.")
            except Exception as exc:
                st.error(str(exc))

        if has("contours_meta"):
            st.json(st.session_state["contours_meta"])
        if has("contours_preview"):
            st.image(st.session_state["contours_preview"], caption="Vista previa curvas/DEM", use_container_width=True)
        if has("contours_kmz"):
            c1, c2 = st.columns(2)
            c1.download_button("Descargar curvas KMZ unificadas", st.session_state["contours_kmz"], file_name="curvas_nivel_unificadas.kmz", mime="application/vnd.google-earth.kmz")
            c2.download_button("Descargar curvas KML unificadas", st.session_state["contours_kml"], file_name="curvas_nivel_unificadas.kml", mime="application/vnd.google-earth.kml+xml")

        if has("basin_kml") and has("contours_kml"):
            st.divider()
            st.subheader("Cuenca + curvas de nivel recortadas")
            st.caption("Salida equivalente al visualizador de cuenca correcta: polígono de cuenca + curvas dentro de la cuenca en un solo KMZ/KML.")
            clip_basin_curves = st.checkbox("Recortar curvas al polígono de cuenca", value=True)
            if st.button("Generar KMZ cuenca + curvas de nivel", type="secondary"):
                try:
                    bc = build_basin_contours_kmz(
                        st.session_state["basin_kml"],
                        st.session_state["contours_kml"],
                        clip_to_basin=bool(clip_basin_curves),
                    )
                    st.session_state["basin_contours_kmz"] = bc.kmz_bytes
                    st.session_state["basin_contours_kml"] = bc.kml_bytes
                    st.session_state["basin_contours_preview"] = bc.preview_png
                    st.session_state["basin_contours_meta"] = bc.metadata
                    save_bytes("cuenca_curvas_nivel.kmz", bc.kmz_bytes)
                    save_bytes("cuenca_curvas_nivel.kml", bc.kml_bytes)
                    if bc.preview_png:
                        save_bytes("preview_cuenca_curvas.png", bc.preview_png)
                    st.success("KMZ cuenca + curvas generado correctamente.")
                except Exception as exc:
                    st.error(str(exc))

        if has("basin_contours_meta"):
            st.json(st.session_state["basin_contours_meta"])
        if has("basin_contours_preview"):
            st.image(st.session_state["basin_contours_preview"], caption="Vista previa cuenca + curvas de nivel", use_container_width=True)
        if has("basin_contours_kmz"):
            c1, c2 = st.columns(2)
            c1.download_button("Descargar KMZ cuenca + curvas de nivel", st.session_state["basin_contours_kmz"], file_name="cuenca_curvas_nivel.kmz", mime="application/vnd.google-earth.kmz")
            c2.download_button("Descargar KML cuenca + curvas de nivel", st.session_state["basin_contours_kml"], file_name="cuenca_curvas_nivel.kml", mime="application/vnd.google-earth.kml+xml")

        st.divider()
        st.subheader("Eje de cauce")
        if has("axis_line"):
            st.success("Eje de cauce cargado desde KMZ/KML.")
        else:
            st.warning("No hay eje cargado. Se puede generar un eje preliminar para continuar.")
            c1, c2 = st.columns(2)
            with c1:
                axis_len = st.number_input("Longitud eje preliminar [km]", min_value=0.1, value=5.0, step=0.5)
            with c2:
                az = st.number_input("Azimut eje preliminar [°]", min_value=0.0, max_value=360.0, value=0.0, step=5.0)
            if st.button("Generar eje preliminar"):
                from modules.axis_sections import generate_preliminary_axis
                cp = st.session_state["control_point"]
                line = generate_preliminary_axis(cp["lon"], cp["lat"], length_km=axis_len, azimuth_deg=az)
                st.session_state["axis_line"] = line
                st.success("Eje preliminar generado.")

with tabs[4]:
    st.header("5 · Generar, rellenar y seleccionar secciones del cauce")
    if not has("axis_line") or not has("dem_path"):
        st.warning("Necesitas DEM y eje de cauce.")
    else:
        st.markdown(
            """
La aplicación genera secciones desde **eje + DEM** y luego aplica control de calidad:

```text
sección bruta
↓
apoyo topográfico opcional
↓
relleno de puntos faltantes
↓
mínimo de puntos válidos
↓
selección de secciones representativas
↓
03_Secciones / 04_Puntos_Seccion
```
"""
        )
        c1, c2, c3 = st.columns(3)
        with c1:
            spacing = st.number_input("Espaciamiento secciones [m]", min_value=5.0, value=100.0, step=10.0)
        with c2:
            width = st.number_input("Ancho sección [m]", min_value=5.0, value=80.0, step=10.0)
        with c3:
            pts_side = st.number_input("Puntos por lado", min_value=2, value=10, step=1)

        st.subheader("Control de calidad y relleno")
        q1, q2, q3, q4 = st.columns(4)
        with q1:
            min_total_points = st.number_input("Mín. puntos totales/sección", min_value=3, value=11, step=2)
        with q2:
            min_valid_points = st.number_input("Mín. puntos válidos/sección", min_value=3, value=9, step=1)
        with q3:
            max_nan_pct = st.number_input("Máx. cotas faltantes [%]", min_value=0.0, max_value=90.0, value=25.0, step=5.0)
        with q4:
            min_width_valid = st.number_input("Ancho útil mínimo [m]", min_value=1.0, value=5.0, step=1.0)

        st.subheader("Uso de curvas topográficas de apoyo")
        t1, t2 = st.columns(2)
        with t1:
            topo_radius = st.number_input("Radio búsqueda apoyo topo [m]", min_value=1.0, value=40.0, step=5.0)
        with t2:
            topo_weight = st.slider("Peso apoyo topográfico", min_value=0.0, max_value=1.0, value=0.70, step=0.05)
        use_topo = (
            has("topo_support_df")
            and isinstance(st.session_state.get("topo_support_df"), pd.DataFrame)
            and not st.session_state["topo_support_df"].empty
            and "z_m" in st.session_state["topo_support_df"].columns
            and st.session_state["topo_support_df"]["z_m"].notna().sum() > 0
        )
        if use_topo:
            st.success("Hay curvas topográficas de apoyo disponibles. Se intentarán usar para mejorar cotas cercanas.")
        else:
            st.info("No hay curvas de apoyo válidas cargadas. Las secciones se generarán solo con DEM y el proceso no se detendrá.")

        if st.button("Generar secciones desde eje + DEM + QA", type="primary"):
            try:
                line = LineString(st.session_state["axis_line"])
                sec_raw, pts_raw = generate_cross_sections(
                    line,
                    st.session_state["dem_path"],
                    spacing_m=float(spacing),
                    width_m=float(width),
                    points_each_side=int(pts_side),
                )
                st.session_state["sections_raw_df"] = sec_raw
                st.session_state["section_points_raw_df"] = pts_raw

                if use_topo:
                    try:
                        pts_adj, topo_report = improve_section_points_with_topo(
                            pts_raw,
                            st.session_state["topo_support_df"],
                            radius_m=float(topo_radius),
                            weight_topo=float(topo_weight),
                        )
                        st.session_state["topo_support_report_df"] = topo_report
                    except Exception as topo_exc:
                        pts_adj = pts_raw
                        st.session_state["topo_support_report_df"] = pd.DataFrame()
                        st.warning(f"Las curvas de apoyo fallaron durante el ajuste de secciones. Se continúa solo con DEM. Detalle: {topo_exc}")
                else:
                    pts_adj = pts_raw
                    st.session_state["topo_support_report_df"] = pd.DataFrame()

                sec_ok, pts_ok, qc_report = select_and_fill_sections(
                    sec_raw,
                    pts_adj,
                    min_valid_points=int(min_valid_points),
                    min_total_points=int(min_total_points),
                    max_nan_pct=float(max_nan_pct),
                    min_wettable_width_m=float(min_width_valid),
                    fill_missing=True,
                )
                st.session_state["sections_df"] = sec_ok
                st.session_state["section_points_df"] = pts_ok
                st.session_state["section_qc_report_df"] = qc_report
                st.session_state["section_qc_summary"] = section_report_summary(qc_report)
                st.success(f"Secciones brutas: {len(sec_raw)} · válidas seleccionadas: {len(sec_ok)}")
            except Exception as exc:
                st.error(str(exc))

        if has("section_qc_summary"):
            sm = st.session_state["section_qc_summary"]
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Secciones totales", sm["n_total"])
            c2.metric("Secciones válidas", sm["n_validas"])
            c3.metric("Descartadas", sm["n_descartadas"])
            c4.metric("% válidas", f"{sm['pct_validas']:.1f}%")

        if has("sections_df"):
            st.subheader("Secciones válidas seleccionadas")
            st.dataframe(st.session_state["sections_df"], use_container_width=True)
            st.subheader("Puntos de sección válidos/rellenados")
            st.dataframe(st.session_state["section_points_df"].head(300), use_container_width=True)

        if has("section_qc_report_df"):
            st.subheader("Reporte QA de secciones")
            st.dataframe(st.session_state["section_qc_report_df"], use_container_width=True)
            st.download_button(
                "Descargar reporte QA secciones CSV",
                st.session_state["section_qc_report_df"].to_csv(index=False).encode("utf-8"),
                file_name="reporte_qaqc_secciones.csv",
                mime="text/csv",
            )

        if has("topo_support_report_df"):
            st.subheader("Reporte uso de curvas topográficas de apoyo")
            st.dataframe(st.session_state["topo_support_report_df"], use_container_width=True)
            st.download_button(
                "Descargar reporte apoyo topográfico CSV",
                st.session_state["topo_support_report_df"].to_csv(index=False).encode("utf-8"),
                file_name="reporte_apoyo_topografico.csv",
                mime="text/csv",
            )


with tabs[5]:
    st.header("6 · Cálculos hidrológicos")
    c1, c2, c3 = st.columns(3)
    basin_m = st.session_state.get("basin_metrics", {})
    area_default = float(basin_m.get("area_km2", 10.0) or 10.0)
    length_default = float(basin_m.get("bbox_largo_km", 5.0) or 5.0)
    with c1:
        area_km2 = st.number_input("Área cuenca [km²]", min_value=0.001, value=area_default, step=1.0)
        C = st.number_input("Coeficiente escorrentía C", min_value=0.01, max_value=1.0, value=0.45, step=0.05)
    with c2:
        length_km = st.number_input("Longitud cauce [km]", min_value=0.001, value=length_default, step=0.5)
        slope = st.number_input("Pendiente media [m/m]", min_value=0.00001, value=0.01, step=0.001, format="%.5f")
    with c3:
        p24_10 = st.number_input("P24,10 [mm]", min_value=0.0, value=80.7, step=1.0)
        alpha = st.number_input("Factor alfa DGA-AC", min_value=0.1, value=2.14, step=0.01)

    tc = time_concentration_kirpich(length_km, slope)
    st.metric("Tiempo concentración Kirpich", f"{tc:.2f} h" if pd.notna(tc) else "N/D")

    st.subheader("Intensidades IDF por periodo [mm/h]")
    periods_txt = st.text_input("Periodos adicionales separados por coma", value="")
    periods = periods_from_text(periods_txt)
    default_int = {2: 20, 5: 28, 10: 35, 25: 45, 50: 55, 100: 65, 200: 75}
    intensities = {}
    cols = st.columns(4)
    for idx, T in enumerate(periods):
        with cols[idx % 4]:
            intensities[float(T)] = st.number_input(f"i T={T:g}", min_value=0.0, value=float(default_int.get(int(T), 35)), step=1.0, key=f"i_{T}")

    if st.button("Calcular hidrología base", type="primary"):
        rat = rational_method(area_km2, C, intensities)
        dga = dga_ac_series(area_km2, p24_10, periods=periods, alpha=alpha)
        st.session_state["hydrology_rational"] = rat
        st.session_state["hydrology_dga"] = dga
        st.session_state["hydrology_inputs"] = {"area_km2": area_km2, "C": C, "length_km": length_km, "slope": slope, "p24_10": p24_10, "alpha": alpha, "tc_h": tc}
        st.session_state["hydrology_done"] = True
        st.success("Hidrología calculada.")

    if has("hydrology_rational"):
        st.subheader("Método racional")
        st.dataframe(st.session_state["hydrology_rational"], use_container_width=True)
        st.subheader("DGA-AC Jp Limarí máx. / alfa")
        st.dataframe(st.session_state["hydrology_dga"], use_container_width=True)

with tabs[6]:
    st.header("7 · Cálculo y adopción de caudales")
    if not has("hydrology_done"):
        st.warning("Primero calcula hidrología.")
    else:
        if st.button("Adoptar caudales por envolvente máxima", type="primary"):
            q = combine_design_flows(st.session_state.get("hydrology_rational"), st.session_state.get("hydrology_dga"))
            st.session_state["q_design"] = q
            st.success("Caudales adoptados.")
        if has("q_design"):
            st.dataframe(st.session_state["q_design"], use_container_width=True)

with tabs[7]:
    st.header("8 · Hidráulica 1D tipo HEC-RAS, socavación y transporte")
    st.markdown(
        """
Este módulo usa las secciones transversales generadas desde el DEM y las resuelve como **sistema conectado**.

La lógica es tipo HEC‑RAS 1D permanente simplificado:

```text
Secciones ordenadas por PK
↓
Condición de borde aguas abajo
↓
Balance de energía entre secciones
↓
Pérdidas por fricción
↓
Pérdidas locales por contracción/expansión
↓
Perfil de cota de agua por periodo de retorno
↓
Shields / MPM / socavación preliminar
```
"""
    )

    if not has("sections_df") or not has("section_points_df") or not has("q_design"):
        st.warning("Necesitas secciones transversales completas y caudales adoptados.")
    else:
        st.subheader("Parámetros de modelación hidráulica conectada")
        c1, c2, c3, c4 = st.columns(4)
        with c1:
            S = st.number_input(
                "Pendiente energía/fricción inicial",
                min_value=0.00001,
                value=float(st.session_state.get("hydrology_inputs", {}).get("slope", 0.01)),
                step=0.001,
                format="%.5f",
            )
        with c2:
            n_default_sup = float(st.session_state.get("n_manning_adoptado", 0.035) or 0.035)
            n = st.number_input("Manning n", min_value=0.010, value=n_default_sup, step=0.005, format="%.3f")
        with c3:
            contr = st.number_input("Coef. contracción", min_value=0.0, max_value=1.0, value=0.10, step=0.05)
        with c4:
            expan = st.number_input("Coef. expansión", min_value=0.0, max_value=1.0, value=0.30, step=0.05)

        c5, c6, c7, c8 = st.columns(4)
        with c5:
            boundary = st.selectbox("Condición aguas abajo", ["tirante_normal", "cota_conocida"], index=0)
        with c6:
            ds_wse = st.number_input("Cota agua aguas abajo [m]", value=0.0, step=0.5, help="Solo se usa si seleccionas cota_conocida.")
        with c7:
            d50 = st.number_input("D50 [m]", min_value=0.0001, value=0.045, step=0.005, format="%.4f")
        with c8:
            d90 = st.number_input("D90 [m]", min_value=0.0001, value=0.20, step=0.01, format="%.3f")

        if st.button("Calcular perfil hidráulico conectado tipo HEC-RAS", type="primary"):
            try:
                profile = hecras_like_steady_profile(
                    st.session_state["sections_df"],
                    st.session_state["section_points_df"],
                    st.session_state["q_design"],
                    n_manning=float(n),
                    downstream_mode=boundary,
                    downstream_wse=float(ds_wse) if boundary == "cota_conocida" else None,
                    slope_energy=float(S),
                    contraction_coeff=float(contr),
                    expansion_coeff=float(expan),
                    alpha=1.0,
                )
                sed = sediment_from_hecras_profile(profile, d50_m=float(d50), d90_m=float(d90), slope_energy=float(S))
                st.session_state["hydraulic_profile_df"] = profile
                st.session_state["hydraulic_df"] = profile
                st.session_state["sediment_df"] = sed
                st.session_state["hecras_like_inputs"] = {
                    "modelo": "1D permanente tipo HEC-RAS simplificado",
                    "n_manning": float(n),
                    "pendiente_energia": float(S),
                    "coef_contraccion": float(contr),
                    "coef_expansion": float(expan),
                    "condicion_aguas_abajo": boundary,
                    "cota_aguas_abajo": float(ds_wse) if boundary == "cota_conocida" else None,
                    "D50_m": float(d50),
                    "D90_m": float(d90),
                }
                st.success("Perfil hidráulico conectado calculado.")
            except Exception as exc:
                st.error(str(exc))

        if has("hydraulic_profile_df"):
            st.subheader("Perfil hidráulico conectado")
            st.dataframe(st.session_state["hydraulic_profile_df"], use_container_width=True)

            try:
                import plotly.express as px
                prof = st.session_state["hydraulic_profile_df"]
                fig = px.line(
                    prof,
                    x="pk_m",
                    y="cota_agua_m",
                    color="T_anios",
                    markers=True,
                    title="Perfil de cota de agua por periodo de retorno",
                    labels={"pk_m": "PK [m]", "cota_agua_m": "Cota agua [m]"},
                )
                st.plotly_chart(fig, use_container_width=True)
            except Exception:
                pass

            st.download_button(
                "Descargar perfil hidráulico CSV",
                st.session_state["hydraulic_profile_df"].to_csv(index=False).encode("utf-8"),
                file_name="perfil_hidraulico_tipo_hecras.csv",
                mime="text/csv",
            )

        if has("sediment_df"):
            st.subheader("Transporte y socavación usando perfil conectado")
            st.dataframe(st.session_state["sediment_df"], use_container_width=True)
            st.download_button(
                "Descargar socavación/sedimentos CSV",
                st.session_state["sediment_df"].to_csv(index=False).encode("utf-8"),
                file_name="socavacion_sedimentos.csv",
                mime="text/csv",
            )

        st.divider()
        st.subheader("Perfil longitudinal 3D con secciones y fenómenos hidráulicos")
        if has("sections_df") and has("section_points_df"):
            v1, v2, v3, v4 = st.columns(4)
            with v1:
                vex = st.slider("Exageración vertical", min_value=0.5, max_value=10.0, value=1.5, step=0.5)
            with v2:
                show_water = st.checkbox("Mostrar lámina de agua", value=True)
            with v3:
                show_scour = st.checkbox("Mostrar socavación", value=True)
            with v4:
                show_depo = st.checkbox("Mostrar depositación", value=True)

            if st.button("Generar perfil longitudinal 3D", type="primary"):
                try:
                    fig3d = create_3d_profile_figure(
                        st.session_state["sections_df"],
                        st.session_state["section_points_df"],
                        hydraulic_df=st.session_state.get("hydraulic_profile_df"),
                        sediment_df=st.session_state.get("sediment_df"),
                        vertical_exaggeration=float(vex),
                        show_water=bool(show_water),
                        show_scour=bool(show_scour),
                        show_deposition=bool(show_depo),
                    )
                    st.session_state["profile_3d_fig"] = fig3d
                    html3d = figure_to_html_bytes(fig3d)
                    st.session_state["profile_3d_html"] = html3d
                    save_bytes("perfil_longitudinal_3d_hidrosed.html", html3d)
                    st.success("Perfil 3D generado.")
                except Exception as exc:
                    st.error(str(exc))

            if has("profile_3d_fig"):
                st.plotly_chart(st.session_state["profile_3d_fig"], use_container_width=True)
            if has("profile_3d_html"):
                st.download_button(
                    "Descargar perfil 3D HTML",
                    st.session_state["profile_3d_html"],
                    file_name="perfil_longitudinal_3d_hidrosed.html",
                    mime="text/html",
                )
        else:
            st.info("Genera primero las secciones transversales.")


        st.warning(
            "Nota técnica: este motor aplica flujo permanente 1D con balance de energía, "
            "pero no reemplaza una modelación HEC‑RAS oficial calibrada. Para diseño final se deben revisar "
            "condiciones de borde, coeficientes, régimen, puentes/alcantarillas, llanuras de inundación y calibración."
        )

with tabs[8]:
    st.header("9 · Lámina cartográfica y exportación final")

    st.subheader("Lámina cartográfica preliminar")
    if not has("dem_path"):
        st.warning("Para generar la lámina necesitas al menos DEM. Para mejor salida agrega cuenca, curvas, eje y morfometría.")
    else:
        c1, c2 = st.columns(2)
        with c1:
            map_title = st.text_input("Título de lámina", value="HidroSed · Delimitación de cuenca y curvas de nivel")
        with c2:
            map_contour_interval = st.selectbox("Curvas visibles en lámina [m]", [1, 2, 5, 10, 20, 25, 50, 100, 200], index=3)

        if st.button("Generar lámina cartográfica PNG", type="primary"):
            try:
                png = make_cartographic_sheet(
                    st.session_state["dem_path"],
                    basin_kml_bytes=st.session_state.get("basin_kml"),
                    axis_line=st.session_state.get("axis_line"),
                    control_point=st.session_state.get("control_point"),
                    metrics=st.session_state.get("basin_metrics"),
                    title=map_title,
                    contour_interval=float(map_contour_interval),
                )
                st.session_state["cartographic_png"] = png
                save_bytes("lamina_cartografica.png", png)
                st.success("Lámina cartográfica generada.")
            except Exception as exc:
                st.error(str(exc))

        if has("cartographic_png"):
            st.image(st.session_state["cartographic_png"], caption="Lámina cartográfica preliminar", use_container_width=True)
            st.download_button("Descargar lámina PNG", st.session_state["cartographic_png"], file_name="lamina_cartografica_hidrosed.png", mime="image/png")

    st.divider()
    st.subheader("Exportables técnicos")
    if has("profile_3d_html"):
        st.download_button(
            "Descargar perfil longitudinal 3D HTML",
            st.session_state["profile_3d_html"],
            file_name="perfil_longitudinal_3d_hidrosed.html",
            mime="text/html",
        )


    if has("basin_metrics_df"):
        st.download_button(
            "Descargar morfometría CSV",
            st.session_state["basin_metrics_df"].to_csv(index=False).encode("utf-8"),
            file_name="morfometria_cuenca.csv",
            mime="text/csv",
        )
    if has("basin_kmz"):
        st.download_button("Descargar cuenca delimitada KMZ", st.session_state["basin_kmz"], file_name="cuenca_delimitada.kmz", mime="application/vnd.google-earth.kmz")
    if has("basin_metrics"):
        st.download_button("Descargar morfometría JSON", json.dumps(st.session_state["basin_metrics"], ensure_ascii=False, indent=2).encode("utf-8"), file_name="morfometria_cuenca.json", mime="application/json")
    if has("section_qc_report_df"):
        st.download_button(
            "Descargar QA secciones CSV",
            st.session_state["section_qc_report_df"].to_csv(index=False).encode("utf-8"),
            file_name="qa_secciones.csv",
            mime="text/csv",
        )
    if has("topo_support_report_df"):
        st.download_button(
            "Descargar apoyo topográfico CSV",
            st.session_state["topo_support_report_df"].to_csv(index=False).encode("utf-8"),
            file_name="apoyo_topografico_secciones.csv",
            mime="text/csv",
        )
    if has("sections_df") and has("section_points_df"):
        xlsx = sections_excel_bytes(
            st.session_state["sections_df"],
            st.session_state["section_points_df"],
            st.session_state.get("q_design"),
            st.session_state.get("hydraulic_df"),
            st.session_state.get("sediment_df"),
        )
        st.download_button("Descargar Excel maestro", xlsx, file_name="HidroSed_Resultados_Maestros.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    if has("contours_kmz"):
        st.download_button("Descargar curvas KMZ", st.session_state["contours_kmz"], file_name="curvas_nivel.kmz")
    if has("axis_kmz_path"):
        p = Path(st.session_state["axis_kmz_path"])
        if p.exists():
            st.download_button("Descargar eje KMZ", p.read_bytes(), file_name="eje_cauce.kmz")
    if has("dem_bytes"):
        st.download_button("Descargar DEM GeoTIFF", st.session_state["dem_bytes"], file_name="dem_hidrosed.tif", mime="image/tiff")

    resumen = {
        "control_point": st.session_state.get("control_point"),
        "basin_metrics": st.session_state.get("basin_metrics"),
        "hydrology_inputs": st.session_state.get("hydrology_inputs"),
        "n_sections": int(len(st.session_state["sections_df"])) if has("sections_df") else 0,
        "n_design_flows": int(len(st.session_state["q_design"])) if has("q_design") else 0,
    }
    st.download_button(
        "Descargar resumen maestro JSON",
        json.dumps(resumen, ensure_ascii=False, indent=2).encode("utf-8"),
        file_name="resumen_maestro_hidrosed.json",
        mime="application/json",
    )

    st.info("Versión integral v2.4: flujo maestro completo configurado para cuencas hasta 10.000 km², curvas mínimo 1 m y perfil hidráulico conectado. Para diseño final se recomienda validar eje, cuenca, secciones y parámetros con antecedentes topográficos/hidrométricos oficiales.")



with tabs[9]:
    st.header("10 · Modo Supremo: rugosidad, granulometría, sección trapezoidal y QA")
    st.markdown(
        """
Este módulo permite avanzar incluso cuando la topografía no entrega secciones suficientes. La app separa claramente resultados **reales/topográficos** de resultados **estimados**.

```text
rugosidad manual / tabla / Cowan / Strickler
↓
sección real o sección trapezoidal estimada
↓
granulometría georreferenciada KMZ
↓
transferencia hidrológica dual
↓
semáforo de confianza
```
"""
    )

    st.subheader("A · Rugosidad avanzada del cauce")
    r1, r2, r3 = st.columns(3)
    with r1:
        rough_mode = st.selectbox("Modo rugosidad", ["manual", "tabla", "cowan", "granulometria/strickler"], index=2)
    with r2:
        cat = st.selectbox("Tipo de cauce", list(ROUGHNESS_TABLE["categoria"]), index=list(ROUGHNESS_TABLE["categoria"]).index("grava_media"))
    with r3:
        has_cal = st.checkbox("Existe calibración nivel/caudal", value=False)

    if rough_mode == "manual":
        a,b,c = st.columns(3)
        with a: n_left = st.number_input("n margen izquierda", min_value=0.010, max_value=0.200, value=0.045, step=0.005, format="%.3f")
        with b: n_ch = st.number_input("n cauce principal", min_value=0.010, max_value=0.200, value=0.038, step=0.005, format="%.3f")
        with c: n_right = st.number_input("n margen derecha", min_value=0.010, max_value=0.200, value=0.045, step=0.005, format="%.3f")
        rough_df = compose_roughness_manual(n_left, n_ch, n_right)
        n_adopt = float(n_ch)
        conf_n = roughness_confidence("manual", has("granulometry_assigned_df"), has_cal, zones=3)
    elif rough_mode == "tabla":
        rough_df = pd.DataFrame([table_n(cat)])
        n_adopt = float(rough_df["n_manning"].iloc[0])
        conf_n = roughness_confidence("tabla", has("granulometry_assigned_df"), has_cal, zones=1)
    elif rough_mode == "cowan":
        c1,c2,c3,c4,c5,c6 = st.columns(6)
        with c1: material = st.selectbox("Material", list(COWAN_FACTORS["n0_material"].keys()), index=3)
        with c2: irr = st.selectbox("Irregularidad", list(COWAN_FACTORS["n1_irregularidad"].keys()), index=2)
        with c3: varsec = st.selectbox("Variación sección", list(COWAN_FACTORS["n2_variacion_seccion"].keys()), index=1)
        with c4: obs = st.selectbox("Obstrucciones", list(COWAN_FACTORS["n3_obstrucciones"].keys()), index=1)
        with c5: veg = st.selectbox("Vegetación", list(COWAN_FACTORS["n4_vegetacion"].keys()), index=1)
        with c6: sinu = st.selectbox("Sinuosidad", list(COWAN_FACTORS["m_sinuosidad"].keys()), index=1)
        rough_df = pd.DataFrame([cowan_n(material, irr, varsec, obs, veg, sinu)])
        n_adopt = float(rough_df["n_manning"].iloc[0])
        conf_n = roughness_confidence("cowan", has("granulometry_assigned_df"), has_cal, zones=3)
    else:
        d50_auto = 0.045
        d84_auto = 0.090
        if has("granulometry_assigned_df") and "D50_m" in st.session_state["granulometry_assigned_df"].columns:
            d50_auto = float(pd.to_numeric(st.session_state["granulometry_assigned_df"]["D50_m"], errors="coerce").median())
        if has("granulometry_assigned_df") and "D84_m" in st.session_state["granulometry_assigned_df"].columns:
            d84_auto = float(pd.to_numeric(st.session_state["granulometry_assigned_df"]["D84_m"], errors="coerce").median())
        rough_df = suggested_roughness(cat, d50_m=d50_auto, d84_m=d84_auto)
        n_adopt = float(rough_df["n_adoptado_recomendado"].dropna().iloc[0])
        conf_n = roughness_confidence("cowan", True, has_cal, zones=3)

    if st.button("Adoptar rugosidad", type="primary"):
        st.session_state["roughness_df"] = rough_df
        st.session_state["n_manning_adoptado"] = n_adopt
        st.session_state["roughness_confidence"] = conf_n
        st.success(f"Rugosidad adoptada n = {n_adopt:.3f} · confianza {conf_n['confianza_rugosidad']}/10")
    st.dataframe(rough_df, use_container_width=True)
    st.json(conf_n)

    st.divider()
    st.subheader("B · Granulometría georreferenciada con KMZ")
    g1, g2 = st.columns(2)
    with g1:
        gran_file = st.file_uploader("Tabla granulométrica CSV/XLSX", type=["csv", "xlsx"], key="gran_table")
    with g2:
        gran_kmz = st.file_uploader("KMZ/KML puntos de muestras", type=["kmz", "kml"], key="gran_kmz")
    if st.button("Leer y validar granulometría"):
        try:
            if gran_file is None:
                raise ValueError("Debes cargar una tabla granulométrica.")
            if gran_file.name.lower().endswith(".csv"):
                gdf = pd.read_csv(gran_file)
            else:
                gdf = pd.read_excel(gran_file)
            gdf = normalize_granulometry_table(gdf)
            if gran_kmz is not None:
                kmltxt = read_kmz_or_kml_to_text(gran_kmz)
                pts = parse_granulometry_points(kmltxt)
                gdf = gdf.merge(pts, on="id_muestra", how="left")
            val = validate_granulometry(gdf)
            st.session_state["granulometry_df"] = gdf
            st.session_state["granulometry_validation_df"] = val
            if has("sections_df"):
                assigned = assign_granulometry_to_sections(st.session_state["sections_df"], gdf)
                st.session_state["granulometry_assigned_df"] = assigned
            st.success("Granulometría leída, validada y asignada por sección si existen secciones.")
        except Exception as exc:
            st.error(str(exc))
    if has("granulometry_df"):
        st.dataframe(st.session_state["granulometry_df"], use_container_width=True)
    if has("granulometry_validation_df"):
        st.dataframe(st.session_state["granulometry_validation_df"], use_container_width=True)
    if has("granulometry_assigned_df"):
        st.subheader("Granulometría asignada por sección")
        st.dataframe(st.session_state["granulometry_assigned_df"], use_container_width=True)

    st.divider()
    st.subheader("C · Sección trapezoidal estimada de respaldo")
    st.caption("Usar cuando no existan suficientes secciones reales. El informe debe marcar estos cálculos como preliminares/estimativos.")
    t1,t2,t3,t4 = st.columns(4)
    with t1:
        btm = st.number_input("Ancho fondo [m]", min_value=0.1, value=6.0, step=0.5)
        reach_len = st.number_input("Longitud tramo [m]", min_value=10.0, value=1000.0, step=100.0)
    with t2:
        dep = st.number_input("Profundidad geométrica [m]", min_value=0.1, value=2.0, step=0.2)
        sep = st.number_input("Separación secciones [m]", min_value=5.0, value=100.0, step=10.0)
    with t3:
        zl = st.number_input("Talud izquierdo H:V", min_value=0.0, value=1.5, step=0.25)
        zr = st.number_input("Talud derecho H:V", min_value=0.0, value=1.5, step=0.25)
    with t4:
        slp = st.number_input("Pendiente longitudinal [m/m]", min_value=0.00001, value=float(st.session_state.get("hydrology_inputs", {}).get("slope", 0.008)), step=0.001, format="%.5f")
        z0 = st.number_input("Cota fondo inicial [m]", value=100.0, step=1.0)
    if st.button("Generar secciones trapezoidales estimadas", type="primary"):
        sec_syn, pts_syn = generate_trapezoid_reach_sections(reach_len, sep, btm, dep, zl, zr, slp, z0_m=z0)
        st.session_state["sections_df"] = sec_syn
        st.session_state["section_points_df"] = pts_syn
        st.session_state["sections_mode"] = "trapezoidal_estimado"
        st.success(f"Secciones trapezoidales generadas: {len(sec_syn)}. El cálculo queda marcado como preliminar estimativo.")
    if has("q_design"):
        qvals = list(pd.to_numeric(st.session_state["q_design"]["Q_m3s"], errors="coerce").dropna())
        if qvals:
            cap = trapezoid_capacity_table(qvals, btm, dep, zl, zr, slp, float(st.session_state.get("n_manning_adoptado", 0.040)))
            st.subheader("Capacidad hidráulica trapezoidal preliminar")
            st.dataframe(cap, use_container_width=True)

    st.divider()
    st.subheader("D · Transferencia hidrológica dual área-altitud-distancia")
    h1,h2,h3,h4 = st.columns(4)
    with h1:
        q_est = st.number_input("Q estación [m³/s]", min_value=0.0, value=10.0, step=1.0)
        a_punto = st.number_input("Área punto [km²]", min_value=0.001, value=float(st.session_state.get("basin_metrics", {}).get("area_km2", 50.0) or 50.0), step=1.0)
    with h2:
        a_est = st.number_input("Área estación [km²]", min_value=0.001, value=60.0, step=1.0, help="Si se calculó desde DEM, ingrese aquí el área obtenida.")
        b_exp = st.number_input("Exponente área b", min_value=0.30, max_value=1.20, value=0.75, step=0.05)
    with h3:
        alt_p = st.number_input("Altitud punto [m]", value=500.0, step=50.0)
        alt_e = st.number_input("Altitud estación [m]", value=450.0, step=50.0)
    with h4:
        dist_km = st.number_input("Distancia estación-punto [km]", min_value=0.0, value=20.0, step=5.0)
    if st.button("Calcular transferencia hidrológica"):
        tr = transfer_flow_area_altitude_distance(q_est, a_punto, a_est, alt_p, alt_e, dist_km, b_exp)
        st.session_state["hydrologic_transfer"] = tr
        st.success(f"Q transferido = {tr.get('Q_transferido_m3s', float('nan')):.2f} m³/s · confianza {tr.get('confianza_transferencia', 0)}/10")
    if has("hydrologic_transfer"):
        st.json(st.session_state["hydrologic_transfer"])

    st.divider()
    st.subheader("E · Semáforo maestro de confianza")
    scores = {
        "DEM / descarga": 8.8 if has("dem_path") else 6.5,
        "Cuenca / morfometría": 8.9 if has("basin_metrics") else 6.0,
        "Curvas / eje": 8.8 if has("contours_kmz") and has("axis_line") else 6.5,
        "Secciones": 8.8 if has("sections_df") and st.session_state.get("sections_mode") != "trapezoidal_estimado" else (7.4 if has("sections_df") else 5.5),
        "Hidrología normativa": 8.9 if has("hydrology_done") else 6.0,
        "Rugosidad": float(st.session_state.get("roughness_confidence", {}).get("confianza_rugosidad", 6.0)),
        "Granulometría": 9.0 if has("granulometry_assigned_df") else 6.5,
        "Hidráulica 1D": 8.8 if has("hydraulic_profile_df") else 6.0,
        "Sedimentos / socavación": 8.8 if has("sediment_df") and has("granulometry_assigned_df") else (7.2 if has("sediment_df") else 5.5),
    }
    conf_df = global_confidence_report(scores)
    st.dataframe(conf_df, use_container_width=True)
    st.session_state["confidence_report_df"] = conf_df
    st.markdown(
        """
<div class='hs-alert'><b>Advertencia técnica:</b> cuando se usen secciones trapezoidales estimadas, los resultados permiten avanzar con prefactibilidad o estimación preliminar, pero no reemplazan levantamiento topográfico ni calibración hidráulica de diseño.</div>
""",
        unsafe_allow_html=True,
    )
