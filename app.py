import streamlit as st
import geopandas as gpd
import pandas as pd
import folium
from folium import FeatureGroup, LayerControl
from shapely.geometry import Polygon
from shapely.validation import make_valid
from urllib.parse import urlencode
from pyproj import Transformer
import mgrs

st.title("Analüüsiala Katastrid + Interaktiivne OSM Kaart")

st.markdown("""
Sisend: 4 MGRS koordinaati  
Väljund: CSV tabel, kokkuvõtted ja interaktiivne kaart.
""")

# Input for MGRS points
st.header("1. Sisend: 4 MGRS nurkapunkti")
pts_mgrs = []
for i in range(4):
    pt = st.text_input(f"MGRS punkt {i+1}", key=f"pt{i}")
    pts_mgrs.append(pt)

if "analysis_result" not in st.session_state:
    st.session_state.analysis_result = None


def run_analysis(pts_mgrs):
    if not all(pts_mgrs):
        raise ValueError("Palun sisesta kõik 4 MGRS punkti.")

    # ============================================================
    # 2. MGRS -> WGS84 -> L-EST97 (EPSG:3301)
    # ============================================================

    m = mgrs.MGRS()
    to_lest97 = Transformer.from_crs("EPSG:4326", "EPSG:3301", always_xy=True)

    def mgrs_to_lest97(code):
        lat, lon = m.toLatLon(code)
        x, y = to_lest97.transform(lon, lat)
        return x, y

    pts_xy = [mgrs_to_lest97(p) for p in pts_mgrs]

    # ============================================================
    # 3. PUNKTIDE JÄRJESTAMINE POLÜGOONI LOOMISEKS
    # ============================================================

    def sort_points_clockwise(points):
        import math
        cx = sum(x for x, y in points) / len(points)
        cy = sum(y for x, y in points) / len(points)

        def angle(p):
            return math.atan2(p[1] - cy, p[0] - cx)

        return sorted(points, key=angle)

    pts_xy_sorted = sort_points_clockwise(pts_xy)

    analysis_poly = Polygon(pts_xy_sorted)

    if not analysis_poly.is_valid:
        analysis_poly = make_valid(analysis_poly)

    if analysis_poly.geom_type != "Polygon":
        raise ValueError("Analüüsiala geomeetria ei moodustanud ühte korrektset polügooni.")

    analysis_gdf = gpd.GeoDataFrame(
        {"nimi": ["Analüüsiala"]},
        geometry=[analysis_poly],
        crs="EPSG:3301"
    )

    # ============================================================
    # 4. BOUNDING BOX WFS PÄRINGUKS
    # ============================================================

    minx, miny, maxx, maxy = analysis_poly.bounds

    # ============================================================
    # 5. WFS FUNKTSIOON KATASTRI LAADIMISEKS
    # ============================================================

    WFS_URL = "https://gsavalik.envir.ee/geoserver/wfs"

    def read_wfs_bbox(type_name, bbox_tuple, crs="EPSG:3301"):
        minx, miny, maxx, maxy = bbox_tuple

        params = {
            "service": "WFS",
            "version": "1.0.0",
            "request": "GetFeature",
            "typeName": type_name,
            "outputFormat": "application/json",
            "srsName": crs,
            "bbox": f"{minx},{miny},{maxx},{maxy}"
        }

        url = f"{WFS_URL}?{urlencode(params)}"
        gdf = gpd.read_file(url, engine="pyogrio")

        if gdf.crs is None:
            gdf = gdf.set_crs(crs, allow_override=True)
        elif str(gdf.crs).upper() != crs.upper():
            gdf = gdf.to_crs(crs)

        return gdf

    # ============================================================
    # 6. LAE KATASTRIÜKSUSED JA LÕIKA TÄPSE ANALÜÜSIALAGA
    # ============================================================

    ky = read_wfs_bbox("kataster:ky_kehtiv", (minx, miny, maxx, maxy), crs="EPSG:3301")
    ky_clip = gpd.clip(ky, analysis_gdf).copy()

    if len(ky_clip) == 0:
        raise ValueError("Selles analüüsialas ei leitud ühtegi katastriüksust.")

    # ============================================================
    # 7. KONTROLLI VAJALIKKE VEERGE
    # ============================================================

    needed_cols = ["tunnus", "omvorm", "ov_nimi", "ay_nimi", "l_aadress", "geometry"]
    missing = [c for c in needed_cols if c not in ky_clip.columns]

    if missing:
        raise ValueError(f"Puuduvad vajalikud veerud: {missing}. Olemas: {list(ky_clip.columns)}")

    # ============================================================
    # 8. PUHASTA TABELI VÄÄRTUSED
    # ============================================================

    for col in ["tunnus", "omvorm", "ov_nimi", "ay_nimi", "l_aadress"]:
        ky_clip[col] = ky_clip[col].fillna("").astype(str).str.strip()

    # ============================================================
    # 9. KOONDA AADRESS
    # ============================================================

    def combine_address_parts(row):
        parts = [row["ov_nimi"], row["ay_nimi"], row["l_aadress"]]
        cleaned = []
        seen = set()

        for part in parts:
            p = str(part).strip()
            if p and p not in seen:
                cleaned.append(p)
                seen.add(p)

        return ", ".join(cleaned) if cleaned else "PUUDUB"

    ky_clip["aadress"] = ky_clip.apply(combine_address_parts, axis=1)

    # ============================================================
    # 10. OMANDIVORMIDE STANDARDISEERIMINE
    # ============================================================

    OMANDIVORMID = [
        "Eraomand",
        "Riigiomand",
        "Munitsipaalomand",
        "Kinnistamata eraomand",
        "Segaomand",
        "Avalik-õiguslik omand",
        "Omandi ulatus selgitamisel"
    ]

    def standardize_omvorm(value):
        if value is None:
            return "Määramata"

        v = str(value).strip().lower()

        if "kinnistamata" in v and "era" in v:
            return "Kinnistamata eraomand"
        if "omandi ulatus" in v and "selgit" in v:
            return "Omandi ulatus selgitamisel"
        if "avalik" in v and "õiguslik" in v:
            return "Avalik-õiguslik omand"
        if "munitsip" in v:
            return "Munitsipaalomand"
        if "riigi" in v:
            return "Riigiomand"
        if "sega" in v:
            return "Segaomand"
        if "era" in v:
            return "Eraomand"
        return "Määramata"

    ky_clip["omvorm_std"] = ky_clip["omvorm"].apply(standardize_omvorm)

    present_omandivormid = [
        om for om in OMANDIVORMID
        if om in ky_clip["omvorm_std"].unique()
    ]

    # ============================================================
    # 11. TABELI KOOSTAMINE
    # ============================================================

    result_df = ky_clip[["tunnus", "omvorm", "aadress"]].copy()
    result_df = result_df.sort_values(by=["omvorm", "tunnus", "aadress"]).reset_index(drop=True)
    csv_data = result_df.to_csv(index=False, sep=";").encode("utf-8-sig")

    # ============================================================
    # 12. OMANDIVORMIDE KOKKUVÕTE
    # ============================================================

    summary_df = (
        ky_clip.groupby("omvorm_std", dropna=False)
        .size()
        .reset_index(name="katastriuksuste_arv")
        .sort_values(by=["katastriuksuste_arv", "omvorm_std"], ascending=[False, True])
        .reset_index(drop=True)
    )

    # ============================================================
    # 12A. OMANDIVORMIDE KOKKUVÕTE PINDAALA JÄRGI
    # ============================================================

    analysis_area_m2 = analysis_gdf.geometry.area.iloc[0]
    analysis_area_km2 = analysis_area_m2 / 1_000_000

    ky_clip["pindala_m2"] = ky_clip.geometry.area
    ky_clip["pindala_km2"] = ky_clip["pindala_m2"] / 1_000_000

    area_summary_df = (
        ky_clip.groupby("omvorm_std", dropna=False)["pindala_km2"]
        .sum()
        .reset_index()
    )

    area_summary_df["osakaal_analyysialast_%"] = (
        area_summary_df["pindala_km2"] / analysis_area_km2 * 100
    )

    area_summary_df["pindala_km2"] = area_summary_df["pindala_km2"].round(6)
    area_summary_df["osakaal_analyysialast_%"] = area_summary_df["osakaal_analyysialast_%"].round(2)

    area_summary_df = area_summary_df.sort_values(
        by=["pindala_km2", "omvorm_std"],
        ascending=[False, True]
    ).reset_index(drop=True)

    # ============================================================
    # 13. FOLIUMI JAOKS TURVALINE GEODATAFRAME
    # ============================================================

    def make_folium_safe(gdf, keep_cols):
        gdf2 = gdf[keep_cols].copy()

        for col in gdf2.columns:
            if col != "geometry":
                gdf2[col] = gdf2[col].fillna("").astype(str)

        gdf2["geometry"] = gdf2["geometry"].apply(
            lambda geom: make_valid(geom) if geom is not None else geom
        )

        return gdf2

    analysis_map = make_folium_safe(analysis_gdf, ["nimi", "geometry"]).to_crs(epsg=4326)

    ky_map = ky_clip[ky_clip["omvorm_std"].isin(OMANDIVORMID)].copy()
    ky_map = make_folium_safe(
        ky_map,
        ["tunnus", "omvorm", "omvorm_std", "aadress", "geometry"]
    ).to_crs(epsg=4326)

    # ============================================================
    # 14. OMANDIVORMIDE VÄRVID
    # ============================================================

    OMANDI_VARVID = {
        "Eraomand": {"fillColor": "#e31a1c", "color": "#8b0000"},
        "Riigiomand": {"fillColor": "#1f78b4", "color": "#0b3c5d"},
        "Munitsipaalomand": {"fillColor": "#33a02c", "color": "#1b5e20"},
        "Kinnistamata eraomand": {"fillColor": "#ff7f00", "color": "#b85c00"},
        "Segaomand": {"fillColor": "#6a3d9a", "color": "#4a235a"},
        "Avalik-õiguslik omand": {"fillColor": "#17a2b8", "color": "#0f6674"},
        "Omandi ulatus selgitamisel": {"fillColor": "#bdbdbd", "color": "#636363"}
    }

    # ============================================================
    # 15. INTERAKTIIVNE KAART
    # ============================================================

    center = analysis_map.geometry.iloc[0].centroid
    center_lat, center_lon = center.y, center.x

    mymap = folium.Map(
        location=[center_lat, center_lon],
        zoom_start=14,
        tiles=None,
        control_scale=True
    )

    folium.TileLayer(
        tiles="OpenStreetMap",
        name="OpenStreetMap",
        overlay=False,
        control=True
    ).add_to(mymap)

    folium.TileLayer(
        tiles="CartoDB positron",
        name="CartoDB Positron",
        overlay=False,
        control=True
    ).add_to(mymap)

    folium.TileLayer(
        tiles="CartoDB voyager",
        name="CartoDB Voyager",
        overlay=False,
        control=True
    ).add_to(mymap)

    folium.TileLayer(
        tiles="https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
        attr="Esri",
        name="Esri Satellite",
        overlay=False,
        control=True
    ).add_to(mymap)

    fg_analysis = FeatureGroup(name="Analüüsiala", show=True)

    folium.GeoJson(
        analysis_map,
        style_function=lambda feature: {
            "color": "blue",
            "weight": 3,
            "fillColor": "blue",
            "fillOpacity": 0.05
        },
        tooltip=folium.GeoJsonTooltip(
            fields=["nimi"],
            aliases=["Kiht:"],
            sticky=False
        )
    ).add_to(fg_analysis)

    fg_analysis.add_to(mymap)

    for omandivorm in present_omandivormid:
        subset = ky_map[ky_map["omvorm_std"] == omandivorm].copy()

        if len(subset) == 0:
            continue

        colors = OMANDI_VARVID[omandivorm]
        fg = FeatureGroup(name=omandivorm, show=True)

        folium.GeoJson(
            subset,
            style_function=lambda feature, fc=colors["fillColor"], ec=colors["color"]: {
                "fillColor": fc,
                "color": ec,
                "weight": 1.0,
                "fillOpacity": 0.60
            },
            tooltip=folium.GeoJsonTooltip(
                fields=["tunnus", "omvorm_std", "aadress"],
                aliases=["Tunnus:", "Omandivorm:", "Aadress:"],
                sticky=False
            ),
            popup=folium.GeoJsonPopup(
                fields=["tunnus", "omvorm", "omvorm_std", "aadress"],
                aliases=["Tunnus:", "Algne omandivorm:", "Standarditud omandivorm:", "Aadress:"],
                localize=True
            )
        ).add_to(fg)

        fg.add_to(mymap)

    bounds = analysis_map.total_bounds
    mymap.fit_bounds([[bounds[1], bounds[0]], [bounds[3], bounds[2]]])
    LayerControl(collapsed=False).add_to(mymap)

    map_html = mymap.get_root().render()

    return {
        "ky_count": len(ky),
        "ky_clip_count": len(ky_clip),
        "present_omandivormid": present_omandivormid,
        "has_maaramaata": "Määramata" in ky_clip["omvorm_std"].unique(),
        "result_df": result_df,
        "summary_df": summary_df,
        "area_summary_df": area_summary_df,
        "analysis_area_km2": analysis_area_km2,
        "csv_data": csv_data,
        "map_html": map_html,
        "html_data": map_html.encode("utf-8"),
    }


if st.button("Käivita analüüs"):
    try:
        st.session_state.analysis_result = run_analysis(pts_mgrs)
    except Exception as e:
        st.session_state.analysis_result = None
        st.error(f"Viga analüüsi käivitamisel: {str(e)}")


result = st.session_state.analysis_result

if result is not None:
    st.write(f"BBox-alusel loetud katastriüksusi: {result['ky_count']}")
    st.write(f"Täpse analüüsialaga lõigatud katastriüksusi: {result['ky_clip_count']}")

    st.write("Analüüsialas esinevad omandivormid:")
    for om in result["present_omandivormid"]:
        st.write(f"- {om}")

    if result["has_maaramaata"]:
        st.write("- Määramata (jäetakse kaardilt välja, kuid jääb tabelisse)")

    st.header("Katastriüksused analüüsialas")
    st.dataframe(result["result_df"])

    st.download_button(
        label="Laadi alla CSV",
        data=result["csv_data"],
        file_name="analyysiala_katastrid.csv",
        mime="text/csv; charset=utf-8",
        on_click="ignore"
    )

    st.header("Omandivormide kokkuvõte (katastriüksuste arv)")
    st.dataframe(result["summary_df"])

    st.header("Omandivormide kokkuvõte pindala järgi")
    st.dataframe(result["area_summary_df"])

    st.write(f"Analüüsiala kogupindala: {result['analysis_area_km2']:.6f} km²")

    st.header("Interaktiivne kaart")
    st.download_button(
        label="Laadi kaart alla HTML-failina",
        data=result["html_data"],
        file_name="analyysiala_interaktiivne_kaart.html",
        mime="text/html; charset=utf-8",
        on_click="ignore"
    )
    st.components.v1.html(result["map_html"], height=600)
