"""
Avance de Producción — MARVA
App genérica de captura y seguimiento de avance de fabricación.

Todo vive en una sola pantalla por proyecto (como el Excel original),
en vez de estar repartido en varias páginas.
"""
import sqlite3
from datetime import date

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import odoo_connector as odoo

DB_PATH = "avance.db"

# ---------------------------------------------------------------------------
# Base de datos
# ---------------------------------------------------------------------------

def get_conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    conn = get_conn()
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS proyectos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nombre TEXT NOT NULL,
        cantidad_total REAL NOT NULL,
        unidad TEXT DEFAULT 'pza',
        fecha TEXT,
        responsable TEXT,
        mo_odoo TEXT,
        activo INTEGER DEFAULT 1
    );
    CREATE TABLE IF NOT EXISTS etapas (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        proyecto_id INTEGER NOT NULL,
        nombre TEXT NOT NULL,
        peso REAL NOT NULL,
        orden INTEGER,
        keywords_odoo TEXT DEFAULT '',
        FOREIGN KEY (proyecto_id) REFERENCES proyectos(id) ON DELETE CASCADE
    );
    CREATE TABLE IF NOT EXISTS materiales (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        proyecto_id INTEGER NOT NULL,
        descripcion TEXT NOT NULL,
        cantidad_total REAL NOT NULL,
        kg_pza REAL DEFAULT 0,
        FOREIGN KEY (proyecto_id) REFERENCES proyectos(id) ON DELETE CASCADE
    );
    CREATE TABLE IF NOT EXISTS avances (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        material_id INTEGER NOT NULL,
        etapa_id INTEGER NOT NULL,
        cantidad_avanzada REAL NOT NULL,
        fecha TEXT NOT NULL,
        FOREIGN KEY (material_id) REFERENCES materiales(id) ON DELETE CASCADE,
        FOREIGN KEY (etapa_id) REFERENCES etapas(id) ON DELETE CASCADE
    );
    """)
    conn.commit()
    return conn


# Etapas por default según el proceso real de Marva
ETAPAS_DEFAULT = [
    ("HABILITADO", 30, "corte,doblez,roscado,barrenado"),
    ("ARMADO", 20, "puntear,armado"),
    ("SOLDADURA", 30, "soldadura"),
    ("PINTURA", 20, "preparacion,fondo,pintura"),
]


def upsert_avance(conn, material_id, etapa_id, cantidad, fecha):
    existing = conn.execute(
        "SELECT id FROM avances WHERE material_id = ? AND etapa_id = ? AND fecha = ?",
        (material_id, etapa_id, fecha),
    ).fetchone()
    if existing:
        conn.execute("UPDATE avances SET cantidad_avanzada = ? WHERE id = ?", (cantidad, existing[0]))
    else:
        conn.execute(
            "INSERT INTO avances (material_id, etapa_id, cantidad_avanzada, fecha) VALUES (?, ?, ?, ?)",
            (material_id, etapa_id, cantidad, fecha),
        )


def get_avance_actual(conn, proyecto_id):
    sql = """
    SELECT m.id AS material_id, m.descripcion, m.cantidad_total, m.kg_pza,
           e.id AS etapa_id, e.nombre AS etapa, e.peso AS peso_etapa, e.orden,
           COALESCE(a.cantidad_avanzada, 0) AS cantidad_avanzada
    FROM materiales m
    CROSS JOIN etapas e ON e.proyecto_id = m.proyecto_id
    LEFT JOIN (
        SELECT material_id, etapa_id, cantidad_avanzada, fecha
        FROM avances a1
        WHERE fecha = (
            SELECT MAX(fecha) FROM avances a2
            WHERE a2.material_id = a1.material_id AND a2.etapa_id = a1.etapa_id
        )
    ) a ON a.material_id = m.id AND a.etapa_id = e.id
    WHERE m.proyecto_id = ?
    ORDER BY e.orden, m.id
    """
    return pd.read_sql_query(sql, conn, params=(proyecto_id,))


def calcular_dashboard(df):
    """% ponderado por KG (o por cantidad si no hay kg_pza), igual que el Excel original."""
    if df.empty:
        return pd.DataFrame(), 0.0
    df = df.copy()
    df["kg_total"] = df["cantidad_total"] * df["kg_pza"]
    df["base"] = df["kg_total"].where(df["kg_pza"] > 0, df["cantidad_total"])
    df["avanzado_base"] = df["cantidad_avanzada"] * df["base"] / df["cantidad_total"].replace(0, pd.NA)
    df["avanzado_base"] = df["avanzado_base"].fillna(0)
    por_etapa = (
        df.groupby(["etapa_id", "etapa", "peso_etapa", "orden"], as_index=False)
        .agg(base_total=("base", "sum"), avanzado_total=("avanzado_base", "sum"))
    )
    por_etapa["pct_etapa"] = (por_etapa["avanzado_total"] / por_etapa["base_total"].replace(0, pd.NA)).fillna(0)
    por_etapa = por_etapa.sort_values("orden")
    pct_total = float((por_etapa["pct_etapa"] * por_etapa["peso_etapa"]).sum()) / 100.0
    return por_etapa, pct_total


def gauge(valor, titulo):
    fig = go.Figure(go.Indicator(
        mode="gauge+number",
        value=valor * 100,
        title={"text": titulo, "font": {"family": "Barlow Condensed"}},
        number={"suffix": "%"},
        gauge={
            "axis": {"range": [0, 100]},
            "bar": {"color": "#D9A441"},
            "bgcolor": "#1D242B",
            "steps": [
                {"range": [0, 50], "color": "#22303E"},
                {"range": [50, 80], "color": "#28405A"},
                {"range": [80, 100], "color": "#2E5478"},
            ],
        },
    ))
    fig.update_layout(height=220, margin=dict(l=20, r=20, t=50, b=10),
                       paper_bgcolor="rgba(0,0,0,0)", font_color="#E8EDF1")
    return fig


# ---------------------------------------------------------------------------
# Página + identidad visual Marva (azul marino + dorado grano)
# ---------------------------------------------------------------------------

st.set_page_config(page_title="MARVA · Avance de Producción", layout="wide", page_icon="🌾")
conn = init_db()

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Barlow+Condensed:wght@500;600;700&family=Inter:wght@400;500;600&display=swap');

:root {
    --marva-bg: #0E1620;
    --marva-panel: #16202B;
    --marva-panel-2: #1C2733;
    --marva-navy: #0F2A47;
    --marva-gold: #D9A441;
    --marva-text: #E8EDF1;
    --marva-muted: #8C99A6;
    --marva-border: #263341;
}
html, body, [class*="css"] { font-family: 'Inter', sans-serif; }
h1, h2, h3, .marva-plate, .marva-plate * { font-family: 'Barlow Condensed', sans-serif; }
.stApp { background-color: var(--marva-bg); }
section[data-testid="stSidebar"] { background-color: var(--marva-panel); border-right: 1px solid var(--marva-border); }

.marva-plate {
    background: linear-gradient(120deg, #0F1B27 0%, #16202B 60%, #0F1B27 100%);
    border: 1px solid var(--marva-border);
    border-left: 6px solid var(--marva-gold);
    border-radius: 6px;
    padding: 16px 24px;
    margin-bottom: 18px;
    display: flex; align-items: center; justify-content: space-between;
}
.marva-plate .brand-name { font-weight: 700; font-size: 28px; letter-spacing: 2px; color: var(--marva-text); }
.marva-plate .brand-tag { font-size: 14px; letter-spacing: 3px; text-transform: uppercase; color: var(--marva-gold); margin-left: 12px; }
.marva-plate .brand-sub { font-family: 'Inter'; font-size: 13px; color: var(--marva-muted); }

div[data-testid="stMetric"] {
    background-color: var(--marva-panel-2);
    border: 1px solid var(--marva-border);
    border-radius: 6px;
    padding: 10px 14px 8px 14px;
    border-top: 3px solid var(--marva-gold);
}
div[data-testid="stMetricLabel"] { color: var(--marva-muted) !important; text-transform: uppercase; letter-spacing: 1px; font-size: 12px !important; }
div[data-testid="stMetricValue"] { color: var(--marva-text) !important; font-family: 'Barlow Condensed'; }

.stButton > button { background-color: var(--marva-navy); color: white; border: 1px solid var(--marva-gold); border-radius: 5px; font-weight: 600; }
.stButton > button:hover { background-color: #163756; color: white; }
.stFormSubmitButton > button { background-color: var(--marva-gold); color: #14181C; border: none; font-weight: 700; }
.stFormSubmitButton > button:hover { background-color: #C4933A; }

div[data-testid="stExpander"] { background-color: var(--marva-panel-2); border: 1px solid var(--marva-border); border-radius: 6px; }
div[data-testid="stProgress"] > div > div > div { background-color: var(--marva-gold) !important; }
hr { border-color: var(--marva-border); }
</style>
""", unsafe_allow_html=True)

st.markdown("""
<div class="marva-plate">
    <div><span class="brand-name">MARVA</span><span class="brand-tag">Producción</span></div>
    <div class="brand-sub">Avance de fabricación · piso de planta</div>
</div>
""", unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Sidebar: proyectos
# ---------------------------------------------------------------------------

proyectos_df = pd.read_sql_query("SELECT * FROM proyectos WHERE activo = 1 ORDER BY id DESC", conn)

st.sidebar.markdown("### 🌾 Proyectos")

if proyectos_df.empty:
    proyecto_id = None
    st.sidebar.info("Crea tu primer proyecto abajo.")
else:
    nombres = proyectos_df.set_index("id")["nombre"].to_dict()
    proyecto_id = st.sidebar.radio("Selecciona uno:", list(nombres.keys()), format_func=lambda x: nombres[x])

with st.sidebar.expander("➕ Nuevo proyecto / pieza", expanded=proyectos_df.empty):
    with st.form("nuevo_proyecto_form"):
        nombre = st.text_input("Nombre de la pieza")
        cantidad_total = st.number_input("Cantidad a producir", min_value=0.0, step=1.0)
        unidad = st.text_input("Unidad", value="pzas")
        responsable = st.text_input("Responsable")
        ahogada = st.checkbox("Pieza ahogada en concreto (pintura = solo fondo gris)")

        if st.form_submit_button("Crear proyecto"):
            if not nombre or cantidad_total <= 0:
                st.error("Falta nombre o cantidad a producir.")
            else:
                cur = conn.execute(
                    "INSERT INTO proyectos (nombre, cantidad_total, unidad, fecha, responsable) VALUES (?,?,?,?,?)",
                    (nombre, cantidad_total, unidad, str(date.today()), responsable),
                )
                nuevo_id = cur.lastrowid
                for i, (et_nombre, peso, keywords) in enumerate(ETAPAS_DEFAULT):
                    if ahogada and et_nombre == "PINTURA":
                        et_nombre, keywords = "PINTURA (FONDO GRIS)", "fondo"
                    conn.execute(
                        "INSERT INTO etapas (proyecto_id, nombre, peso, orden, keywords_odoo) VALUES (?,?,?,?,?)",
                        (nuevo_id, et_nombre, peso, i, keywords),
                    )
                conn.commit()
                st.success(f"'{nombre}' creado.")
                st.rerun()

st.sidebar.caption("Base de datos local: avance.db (SQLite). Un solo archivo para todos los proyectos.")

if not proyecto_id:
    st.info("👈 Crea o selecciona un proyecto en el menú de la izquierda para empezar.")
    st.stop()

proyecto = proyectos_df.set_index("id").loc[proyecto_id]
etapas_df = pd.read_sql_query("SELECT * FROM etapas WHERE proyecto_id = ? ORDER BY orden", conn, params=(proyecto_id,))
etapas = etapas_df.to_dict("records")

# ---------------------------------------------------------------------------
# Encabezado del proyecto
# ---------------------------------------------------------------------------

avance_df = get_avance_actual(conn, proyecto_id)
resumen_etapas, pct_total = calcular_dashboard(avance_df)

c1, c2, c3, c4 = st.columns(4)
c1.metric("Pieza", proyecto["nombre"])
c2.metric("Cantidad a producir", f"{proyecto['cantidad_total']:g} {proyecto['unidad']}")
c3.metric("Responsable", proyecto["responsable"] or "—")
c4.metric("Avance total", f"{pct_total*100:.1f}%")

st.divider()

# ---------------------------------------------------------------------------
# Etapas / pesos / mapeo a centros de trabajo de Odoo (colapsado)
# ---------------------------------------------------------------------------

with st.expander("⚙️ Etapas, pesos y centros de trabajo de Odoo"):
    st.caption(
        "El peso % de cada etapa se usa para ponderar el avance total. Las 'palabras clave' son los "
        "nombres (o parte de ellos) de los centros de trabajo en Odoo que corresponden a esa etapa — "
        "así la sincronización sabe a cuál etapa suma cada operación."
    )
    etapas_editable = pd.DataFrame(etapas)[["id", "nombre", "peso", "keywords_odoo"]]
    etapas_editadas = st.data_editor(
        etapas_editable, use_container_width=True, hide_index=True, disabled=["id"], key="etapas_editor"
    )
    if st.button("Guardar etapas"):
        suma = etapas_editadas["peso"].sum()
        if abs(suma - 100) > 0.5:
            st.error(f"Los pesos suman {suma:.0f}%, deben sumar 100%.")
        else:
            for _, row in etapas_editadas.iterrows():
                conn.execute(
                    "UPDATE etapas SET nombre=?, peso=?, keywords_odoo=? WHERE id=?",
                    (row["nombre"], row["peso"], row["keywords_odoo"], int(row["id"])),
                )
            conn.commit()
            st.success("Etapas actualizadas.")
            st.rerun()

# ---------------------------------------------------------------------------
# Importar materiales (manual / archivo / Odoo BOM) — colapsado
# ---------------------------------------------------------------------------

with st.expander("📦 Agregar materiales"):
    imp_tab1, imp_tab2, imp_tab3 = st.tabs(["Manual", "Archivo (CSV/Excel)", "Traer BOM de Odoo"])

    with imp_tab1:
        with st.form("nuevo_material_form", clear_on_submit=True):
            mc1, mc2, mc3 = st.columns(3)
            desc = mc1.text_input("Descripción")
            cant = mc2.number_input("Cantidad total", min_value=0.0, step=1.0)
            kg_pza = mc3.number_input("Kg/pza (opcional)", min_value=0.0, step=0.1)
            if st.form_submit_button("Agregar"):
                if desc and cant > 0:
                    conn.execute(
                        "INSERT INTO materiales (proyecto_id, descripcion, cantidad_total, kg_pza) VALUES (?,?,?,?)",
                        (proyecto_id, desc, cant, kg_pza),
                    )
                    conn.commit()
                    st.rerun()
                else:
                    st.error("Falta descripción o cantidad.")

    with imp_tab2:
        st.caption("Columnas esperadas: descripcion, cantidad_total, kg_pza (kg_pza es opcional)")
        archivo = st.file_uploader("Subir archivo", type=["csv", "xlsx"])
        if archivo is not None:
            df_up = pd.read_csv(archivo) if archivo.name.endswith(".csv") else pd.read_excel(archivo)
            df_up.columns = [c.strip().lower() for c in df_up.columns]
            if st.button("Importar filas"):
                n = 0
                for _, row in df_up.iterrows():
                    if pd.notna(row.get("descripcion")) and pd.notna(row.get("cantidad_total")):
                        conn.execute(
                            "INSERT INTO materiales (proyecto_id, descripcion, cantidad_total, kg_pza) VALUES (?,?,?,?)",
                            (proyecto_id, row["descripcion"], float(row["cantidad_total"]), float(row.get("kg_pza", 0) or 0)),
                        )
                        n += 1
                conn.commit()
                st.success(f"{n} materiales importados.")
                st.rerun()

    with imp_tab3:
        if odoo.is_odoo_configured():
            st.success("Odoo conectado (credenciales en Secrets).")
            odoo_url, odoo_db, odoo_user, odoo_pass = odoo.get_credentials_from_secrets()
        else:
            st.caption("Sin credenciales guardadas — mételas aquí (solo viven en esta sesión).")
            oc1, oc2 = st.columns(2)
            odoo_url = oc1.text_input("URL de Odoo", placeholder="https://tuempresa.odoo.com")
            odoo_db = oc2.text_input("Base de datos")
            oc3, oc4 = st.columns(2)
            odoo_user = oc3.text_input("Usuario")
            odoo_pass = oc4.text_input("Contraseña / API key", type="password")

        codigo_producto = st.text_input("Código o nombre del producto en Odoo")
        if st.button("Traer BOM de Odoo"):
            if not (odoo_url and odoo_db and odoo_user and odoo_pass and codigo_producto):
                st.error("Faltan datos de conexión o el código del producto.")
            else:
                try:
                    materiales_odoo = odoo.fetch_bom_from_odoo(odoo_url, odoo_db, odoo_user, odoo_pass, codigo_producto)
                    for mat in materiales_odoo:
                        conn.execute(
                            "INSERT INTO materiales (proyecto_id, descripcion, cantidad_total, kg_pza) VALUES (?,?,?,?)",
                            (proyecto_id, mat["descripcion"], mat["cantidad_total"], mat["kg_pza"]),
                        )
                    conn.commit()
                    st.success(f"Se importaron {len(materiales_odoo)} materiales desde Odoo.")
                    st.rerun()
                except Exception as e:
                    st.error(f"No se pudo traer la BOM: {e}")

# ---------------------------------------------------------------------------
# Sincronizar AVANCE con Odoo (por centro de trabajo / orden de fabricación)
# ---------------------------------------------------------------------------

with st.expander("🔄 Sincronizar avance con Odoo (por orden de fabricación)", expanded=False):
    st.caption(
        "Trae cuánto llevas avanzado directo de la Orden de Fabricación en Odoo, agrupado por centro de "
        "trabajo, y lo reparte entre las etapas según las palabras clave que configuraste arriba. "
        "Aplica el mismo % a todos los materiales de esa etapa."
    )
    if odoo.is_odoo_configured():
        odoo_url, odoo_db, odoo_user, odoo_pass = odoo.get_credentials_from_secrets()
    else:
        st.warning("Configura tus credenciales de Odoo en la sección 'Agregar materiales' → 'Traer BOM de Odoo', o en Secrets.")
        odoo_url = odoo_db = odoo_user = odoo_pass = None

    mo_ref = st.text_input("Referencia de la Orden de Fabricación en Odoo (ej. WH/MO/00123)",
                            value=proyecto["mo_odoo"] or "")

    if st.button("Sincronizar avance"):
        if not (odoo_url and odoo_db and odoo_user and odoo_pass and mo_ref):
            st.error("Faltan datos de conexión o la referencia de la orden.")
        else:
            try:
                resultado = odoo.fetch_avance_por_centro(odoo_url, odoo_db, odoo_user, odoo_pass, mo_ref)
                conn.execute("UPDATE proyectos SET mo_odoo = ? WHERE id = ?", (mo_ref, proyecto_id))

                por_centro = resultado["por_centro"]
                cantidad_total_orden = resultado["cantidad_total"] or proyecto["cantidad_total"]
                materiales = pd.read_sql_query("SELECT * FROM materiales WHERE proyecto_id = ?", conn, params=(proyecto_id,))
                hoy = str(date.today())

                resumen_sync = []
                for et in etapas:
                    kw_list = [k.strip().lower() for k in (et["keywords_odoo"] or "").split(",") if k.strip()]
                    qty_etapa = sum(
                        v for centro, v in por_centro.items()
                        if any(kw in centro.lower() for kw in kw_list)
                    )
                    pct = min(qty_etapa / cantidad_total_orden, 1.0) if cantidad_total_orden else 0.0
                    resumen_sync.append((et["nombre"], qty_etapa, pct * 100))
                    for _, mat in materiales.iterrows():
                        cantidad_avanzada = pct * mat["cantidad_total"]
                        upsert_avance(conn, int(mat["id"]), int(et["id"]), cantidad_avanzada, hoy)

                conn.commit()
                st.success(f"Sincronizado con la orden '{resultado['orden']}'.")
                st.dataframe(pd.DataFrame(resumen_sync, columns=["Etapa", "Piezas avanzadas (Odoo)", "% aplicado"]),
                             hide_index=True, use_container_width=True)
                st.rerun()
            except Exception as e:
                st.error(f"No se pudo sincronizar: {e}")

# ---------------------------------------------------------------------------
# Tabla única: materiales + captura de avance por etapa (estilo Excel)
# ---------------------------------------------------------------------------

st.subheader("Materiales y avance por etapa")

materiales_df = pd.read_sql_query("SELECT * FROM materiales WHERE proyecto_id = ? ORDER BY id", conn, params=(proyecto_id,))

if materiales_df.empty:
    st.info("Todavía no hay materiales. Ábrelo en '📦 Agregar materiales' arriba.")
else:
    tabla = materiales_df[["id", "descripcion", "cantidad_total", "kg_pza"]].copy()
    tabla = tabla.rename(columns={"descripcion": "Material", "cantidad_total": "Cant. Total", "kg_pza": "Kg/pza"})

    for et in etapas:
        avance_et = avance_df[avance_df["etapa_id"] == et["id"]][["material_id", "cantidad_avanzada"]]
        avance_et = avance_et.rename(columns={"cantidad_avanzada": et["nombre"]})
        tabla = tabla.merge(avance_et, left_on="id", right_on="material_id", how="left").drop(columns=["material_id"])
        tabla[et["nombre"]] = tabla[et["nombre"]].fillna(0)

    tabla_editada = st.data_editor(
        tabla, use_container_width=True, hide_index=True, disabled=["id"],
        num_rows="dynamic", key="tabla_materiales_editor",
    )

    if st.button("💾 Guardar cambios"):
        ids_originales = set(tabla["id"].dropna().astype(int))
        ids_editados = set(tabla_editada["id"].dropna().astype(int))
        for mid in ids_originales - ids_editados:
            conn.execute("DELETE FROM materiales WHERE id = ?", (int(mid),))

        hoy = str(date.today())
        for _, row in tabla_editada.iterrows():
            if pd.isna(row["id"]):
                if not row["Material"] or not row["Cant. Total"]:
                    continue
                cur = conn.execute(
                    "INSERT INTO materiales (proyecto_id, descripcion, cantidad_total, kg_pza) VALUES (?,?,?,?)",
                    (proyecto_id, row["Material"], row["Cant. Total"], row["Kg/pza"] or 0),
                )
                mid = cur.lastrowid
            else:
                mid = int(row["id"])
                conn.execute(
                    "UPDATE materiales SET descripcion=?, cantidad_total=?, kg_pza=? WHERE id=?",
                    (row["Material"], row["Cant. Total"], row["Kg/pza"] or 0, mid),
                )
            for et in etapas:
                valor = row.get(et["nombre"], 0) or 0
                upsert_avance(conn, mid, et["id"], valor, hoy)

        conn.commit()
        st.success("Guardado.")
        st.rerun()

# ---------------------------------------------------------------------------
# Dashboard (en la misma página)
# ---------------------------------------------------------------------------

st.divider()
st.subheader("Dashboard")

if materiales_df.empty:
    st.info("Sin materiales todavía — no hay nada que mostrar.")
else:
    dg1, dg2 = st.columns([1, 2])
    with dg1:
        st.plotly_chart(gauge(pct_total, "Avance total"), use_container_width=True)
    with dg2:
        fig_bar = go.Figure(go.Bar(
            x=resumen_etapas["etapa"], y=resumen_etapas["pct_etapa"] * 100,
            text=[f"{v:.1f}%" for v in resumen_etapas["pct_etapa"] * 100],
            textposition="outside", marker_color="#D9A441",
        ))
        fig_bar.update_layout(yaxis_range=[0, 100], height=220, margin=dict(l=20, r=20, t=20, b=20),
                               paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)", font_color="#E8EDF1")
        st.plotly_chart(fig_bar, use_container_width=True)

    faltante = proyecto["cantidad_total"] * (1 - pct_total)
    st.metric("Falta por completar (equivalente ponderado)", f"{faltante:.0f} {proyecto['unidad']}")

# ---------------------------------------------------------------------------
# Administrar proyecto
# ---------------------------------------------------------------------------

with st.expander("🗄️ Administrar este proyecto"):
    st.caption("**Archivar** lo saca de la lista pero no borra nada. **Eliminar** lo borra por completo, sin deshacer.")
    ac1, ac2 = st.columns(2)
    with ac1:
        if st.button("Archivar proyecto"):
            conn.execute("UPDATE proyectos SET activo = 0 WHERE id = ?", (proyecto_id,))
            conn.commit()
            st.rerun()
    with ac2:
        confirmar = st.checkbox(f"Confirmo eliminar '{proyecto['nombre']}' definitivamente")
        if st.button("Eliminar definitivamente", disabled=not confirmar):
            conn.execute("DELETE FROM proyectos WHERE id = ?", (proyecto_id,))
            conn.commit()
            st.rerun()
