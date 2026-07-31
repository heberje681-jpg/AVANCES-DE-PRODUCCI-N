"""
Avance de Producción — MARVA
App genérica de captura y seguimiento de avance de fabricación.

El avance se mide a nivel PIEZA por etapa (cuántas piezas de las que
hay que producir ya pasaron por Habilitado, Armado, Soldadura,
Pintura...), no por material — los materiales son solo la lista de
insumos que se necesitan (referencia), no algo que "avanza" por etapa.
"""
import base64
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
    CREATE TABLE IF NOT EXISTS avance_etapas (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        etapa_id INTEGER NOT NULL,
        cantidad_avanzada REAL NOT NULL,
        fecha TEXT NOT NULL,
        FOREIGN KEY (etapa_id) REFERENCES etapas(id) ON DELETE CASCADE
    );
    """)
    conn.commit()
    _migrar_columnas_faltantes(conn)
    return conn


def _migrar_columnas_faltantes(conn):
    """Si la base ya existía de una versión anterior de la app, le agrega
    las columnas que falten en vez de tronar."""
    columnas_esperadas = {
        "proyectos": {
            "unidad": "TEXT DEFAULT 'pza'",
            "fecha": "TEXT",
            "responsable": "TEXT",
            "mo_odoo": "TEXT",
            "activo": "INTEGER DEFAULT 1",
        },
        "etapas": {
            "orden": "INTEGER",
            "keywords_odoo": "TEXT DEFAULT ''",
        },
        "materiales": {
            "kg_pza": "REAL DEFAULT 0",
        },
    }
    for tabla, columnas in columnas_esperadas.items():
        existentes = {fila[1] for fila in conn.execute(f"PRAGMA table_info({tabla})").fetchall()}
        for columna, tipo in columnas.items():
            if columna not in existentes:
                conn.execute(f"ALTER TABLE {tabla} ADD COLUMN {columna} {tipo}")
    conn.commit()


# Etapas por default según el proceso real de Marva
ETAPAS_DEFAULT = [
    ("HABILITADO", 30, "excalibur,pantografo,metalero,roladora,dobladora,sierra,taladro,cizalla,tarraja"),
    ("ARMADO", 20, "armado,puntear"),
    ("SOLDADURA", 30, "soldadora,soldadura"),
    ("PINTURA", 20, "pintura,preparacion,fondo"),
]


def upsert_avance_etapa(conn, etapa_id, cantidad, fecha):
    existing = conn.execute(
        "SELECT id FROM avance_etapas WHERE etapa_id = ? AND fecha = ?", (etapa_id, fecha)
    ).fetchone()
    if existing:
        conn.execute("UPDATE avance_etapas SET cantidad_avanzada = ? WHERE id = ?", (cantidad, existing[0]))
    else:
        conn.execute(
            "INSERT INTO avance_etapas (etapa_id, cantidad_avanzada, fecha) VALUES (?, ?, ?)",
            (etapa_id, cantidad, fecha),
        )


def get_avance_etapas(conn, proyecto_id):
    sql = """
    SELECT e.id AS etapa_id, e.nombre AS etapa, e.peso, e.orden, e.keywords_odoo,
           COALESCE(a.cantidad_avanzada, 0) AS cantidad_avanzada
    FROM etapas e
    LEFT JOIN (
        SELECT etapa_id, cantidad_avanzada, fecha
        FROM avance_etapas a1
        WHERE fecha = (SELECT MAX(fecha) FROM avance_etapas a2 WHERE a2.etapa_id = a1.etapa_id)
    ) a ON a.etapa_id = e.id
    WHERE e.proyecto_id = ?
    ORDER BY e.orden
    """
    return pd.read_sql_query(sql, conn, params=(proyecto_id,))


def calcular_dashboard(df_etapas, cantidad_total_proyecto):
    """% simple: piezas avanzadas / piezas totales, ponderado por el peso de cada etapa."""
    if df_etapas.empty or not cantidad_total_proyecto:
        return df_etapas.assign(pct_etapa=0.0), 0.0
    df = df_etapas.copy()
    df["pct_etapa"] = (df["cantidad_avanzada"] / cantidad_total_proyecto).clip(upper=1.0)
    pct_total = float((df["pct_etapa"] * df["peso"]).sum()) / 100.0
    return df, pct_total


def gauge(valor, titulo):
    fig = go.Figure(go.Indicator(
        mode="gauge+number",
        value=valor * 100,
        title={"text": titulo, "font": {"family": "Barlow Condensed"}},
        number={"suffix": "%"},
        gauge={
            "axis": {"range": [0, 100]},
            "bar": {"color": "#2E7BC4"},
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
    --marva-bg: #0E1620; --marva-panel: #16202B; --marva-panel-2: #1C2733;
    --marva-navy: #00366C; --marva-accent: #2E7BC4; --marva-text: #E8EDF1;
    --marva-muted: #8C99A6; --marva-border: #263341; --marva-black: #0A0A0A;
}
html, body, [class*="css"] { font-family: 'Inter', sans-serif; }
h1, h2, h3, .marva-plate, .marva-plate * { font-family: 'Barlow Condensed', sans-serif; }
.stApp { background-color: var(--marva-bg); }
section[data-testid="stSidebar"] { background-color: var(--marva-panel); border-right: 1px solid var(--marva-border); }
.marva-plate {
    background: linear-gradient(120deg, #0F1B27 0%, #16202B 60%, #0F1B27 100%);
    border: 1px solid var(--marva-border); border-left: 6px solid var(--marva-accent); border-radius: 6px;
    padding: 16px 24px; margin-bottom: 18px; display: flex; align-items: center; justify-content: space-between;
}
.marva-plate .brand-name { font-weight: 700; font-size: 28px; letter-spacing: 2px; color: var(--marva-text); }
.marva-plate .brand-tag { font-size: 14px; letter-spacing: 3px; text-transform: uppercase; color: var(--marva-accent); margin-left: 12px; }
.marva-plate .brand-sub { font-family: 'Inter'; font-size: 13px; color: var(--marva-muted); }
div[data-testid="stMetric"] {
    background-color: var(--marva-panel-2); border: 1px solid var(--marva-border); border-radius: 6px;
    padding: 10px 14px 8px 14px; border-top: 3px solid var(--marva-accent);
}
div[data-testid="stMetricLabel"] { color: var(--marva-muted) !important; text-transform: uppercase; letter-spacing: 1px; font-size: 12px !important; }
div[data-testid="stMetricValue"] { color: var(--marva-text) !important; font-family: 'Barlow Condensed'; }
.stButton > button { background-color: var(--marva-navy); color: white; border: 1px solid var(--marva-accent); border-radius: 5px; font-weight: 600; }
.stButton > button:hover { background-color: #163756; color: white; }
.stFormSubmitButton > button { background-color: var(--marva-accent); color: #14181C; border: none; font-weight: 700; }
.stFormSubmitButton > button:hover { background-color: #C4933A; }
div[data-testid="stExpander"] { background-color: var(--marva-panel-2); border: 1px solid var(--marva-border); border-radius: 6px; }
div[data-testid="stProgress"] > div > div > div { background-color: var(--marva-accent) !important; }
hr { border-color: var(--marva-border); }

/* --- Responsive: celular y pantallas angostas --- */
@media (max-width: 680px) {
    .block-container { padding-left: 0.8rem !important; padding-right: 0.8rem !important; padding-top: 1rem !important; }
    div[data-testid="stHorizontalBlock"] { flex-direction: column; gap: 0.5rem !important; }
    div[data-testid="stHorizontalBlock"] > div { width: 100% !important; min-width: 100% !important; }
    .marva-plate { flex-direction: column; align-items: flex-start; gap: 6px; padding: 14px 16px; }
    .marva-plate .brand-name { font-size: 22px; }
    .marva-plate .brand-tag { margin-left: 0; }
    div[data-testid="stMetricValue"] { font-size: 20px !important; }
    div[data-testid="stMetric"] { padding: 8px 10px 6px 10px; }
    .stButton > button, .stFormSubmitButton > button { width: 100%; padding: 0.6rem; font-size: 15px; }
    h2, h3 { font-size: 20px !important; }
}
</style>
""", unsafe_allow_html=True)

def _logo_html():
    try:
        with open("marva_logo.png", "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        return f'<img src="data:image/png;base64,{b64}" style="height:38px;margin-right:14px;">'
    except FileNotFoundError:
        return ""


st.markdown(f"""
<div class="marva-plate">
    <div style="display:flex; align-items:center;">
        {_logo_html()}
        <div><span class="brand-name">MARVA</span><span class="brand-tag">Producción</span></div>
    </div>
    <div class="brand-sub">Avance de fabricación · piso de planta</div>
</div>
""", unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Sidebar: proyectos
# ---------------------------------------------------------------------------

proyectos_df = pd.read_sql_query("SELECT * FROM proyectos WHERE activo = 1 ORDER BY id DESC", conn)

if not proyectos_df.empty:
    with st.expander("📊 Vista general de todos los proyectos activos", expanded=False):
        filas_resumen = []
        for _, p in proyectos_df.iterrows():
            et_p = get_avance_etapas(conn, p["id"])
            _, pct_p = calcular_dashboard(et_p, p["cantidad_total"])
            filas_resumen.append({
                "Proyecto": p["nombre"],
                "Cantidad": f"{p['cantidad_total']:g} {p['unidad']}",
                "Responsable": p["responsable"] or "—",
                "% Avance": pct_p,
            })
        resumen_general_df = pd.DataFrame(filas_resumen)
        st.dataframe(
            resumen_general_df,
            column_config={
                "% Avance": st.column_config.ProgressColumn("% Avance", min_value=0, max_value=1, format="%.0f%%")
            },
            use_container_width=True, hide_index=True,
        )

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
        cantidad_total = st.number_input("Cantidad a producir (piezas)", min_value=0.0, step=1.0)
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
etapas_avance = get_avance_etapas(conn, proyecto_id)
etapas_avance, pct_total = calcular_dashboard(etapas_avance, proyecto["cantidad_total"])
etapas = etapas_avance.to_dict("records")

# ---------------------------------------------------------------------------
# Encabezado
# ---------------------------------------------------------------------------

c1, c2, c3, c4 = st.columns(4)
c1.metric("Pieza", proyecto["nombre"])
c2.metric("Cantidad a producir", f"{proyecto['cantidad_total']:g} {proyecto['unidad']}")
c3.metric("Responsable", proyecto["responsable"] or "—")
c4.metric("Avance total", f"{pct_total*100:.1f}%")

st.divider()

# ---------------------------------------------------------------------------
# Etapas / pesos / centros de trabajo (colapsado)
# ---------------------------------------------------------------------------

with st.expander("⚙️ Etapas, pesos y centros de trabajo de Odoo"):
    st.caption(
        "El peso % de cada etapa pondera el avance total. Las 'palabras clave' son los nombres (o "
        "parte de ellos) de los centros de trabajo en Odoo que corresponden a esa etapa."
    )
    etapas_editable = etapas_avance[["etapa_id", "etapa", "peso", "keywords_odoo"]].rename(
        columns={"etapa_id": "id", "etapa": "nombre"}
    )
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
# Materiales (referencia / BOM) — informativo, ya no maneja avance
# ---------------------------------------------------------------------------

with st.expander("📦 Materiales necesarios (referencia, no afecta el % de avance)"):
    st.caption(
        "Esta lista es solo de consulta — qué insumos se necesitan para fabricar la pieza. "
        "El avance de producción se captura por etapa, más abajo."
    )
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

    materiales_df = pd.read_sql_query("SELECT * FROM materiales WHERE proyecto_id = ? ORDER BY id", conn, params=(proyecto_id,))
    if not materiales_df.empty:
        st.dataframe(
            materiales_df[["descripcion", "cantidad_total", "kg_pza"]].rename(
                columns={"descripcion": "Material", "cantidad_total": "Cantidad", "kg_pza": "Kg/pza"}
            ),
            use_container_width=True, hide_index=True,
        )

# ---------------------------------------------------------------------------
# Captura de avance por etapa — el corazón de la app
# ---------------------------------------------------------------------------

st.subheader("Avance por etapa")
st.caption(f"De {proyecto['cantidad_total']:g} {proyecto['unidad']} a producir, ¿cuántas ya pasaron por cada etapa?")

with st.form("captura_avance_form"):
    valores_nuevos = {}
    for et in etapas:
        col1, col2 = st.columns([3, 1])
        with col1:
            valores_nuevos[et["etapa_id"]] = st.number_input(
                f"{et['etapa']} (peso {et['peso']:.0f}%)",
                min_value=0.0, max_value=float(proyecto["cantidad_total"]),
                value=float(et["cantidad_avanzada"]), step=1.0,
                key=f"avance_{et['etapa_id']}",
            )
        with col2:
            pct_et = min(valores_nuevos[et["etapa_id"]] / proyecto["cantidad_total"], 1.0) * 100 if proyecto["cantidad_total"] else 0
            st.metric("% etapa", f"{pct_et:.0f}%", label_visibility="collapsed")

    if st.form_submit_button("💾 Guardar avance"):
        hoy = str(date.today())
        for etapa_id, valor in valores_nuevos.items():
            upsert_avance_etapa(conn, etapa_id, valor, hoy)
        conn.commit()
        st.rerun()

# ---------------------------------------------------------------------------
# Ver operaciones y sincronizar avance con Odoo (por orden de fabricación)
# ---------------------------------------------------------------------------

with st.expander("🔄 Operaciones de Odoo (ver checklist / sincronizar avance)"):
    st.caption(
        "Marva no captura cantidades por operación en Odoo, solo si cada estación ya se hizo o no "
        "(y no te deja pasar a la siguiente sin terminar la anterior). Esto trae ese checklist real, "
        "y si quieres, reparte el % de cada etapa según cuántas de sus estaciones ya están hechas."
    )
    if odoo.is_odoo_configured():
        odoo_url, odoo_db, odoo_user, odoo_pass = odoo.get_credentials_from_secrets()
    else:
        st.warning("Configura tus credenciales de Odoo en 'Materiales necesarios' → 'Traer BOM de Odoo', o en Secrets.")
        odoo_url = odoo_db = odoo_user = odoo_pass = None

    mo_ref = st.text_input(
        "Orden de fabricación en Odoo (o pega el mismo código/nombre que usaste para la BOM)",
        value=proyecto["mo_odoo"] or "",
        help="Acepta la referencia exacta de la orden (WH/MO/00123), el código del producto, "
             "o el texto tal cual lo copias de Odoo. Si no hay match exacto de orden, toma la más reciente de ese producto.",
    )

    bc1, bc2 = st.columns(2)
    ver_operaciones = bc1.button("👁️ Ver operaciones (solo consulta)")
    sincronizar = bc2.button("💾 Ver y aplicar avance a las etapas")

    if ver_operaciones or sincronizar:
        if not (odoo_url and odoo_db and odoo_user and odoo_pass and mo_ref):
            st.error("Faltan datos de conexión o la referencia de la orden.")
        else:
            try:
                resultado = odoo.fetch_avance_por_centro(odoo_url, odoo_db, odoo_user, odoo_pass, mo_ref)
                conn.execute("UPDATE proyectos SET mo_odoo = ? WHERE id = ?", (mo_ref, proyecto_id))
                conn.commit()

                st.markdown(f"**Orden: {resultado['orden']}**")
                ops_df = pd.DataFrame(resultado["operaciones"])[["operacion", "centro", "estado"]]
                ops_df["estado"] = ops_df["estado"].map(
                    {"done": "✅ Hecha", "progress": "🔧 En proceso", "ready": "⏳ Pendiente", "pending": "⏳ Pendiente"}
                ).fillna(ops_df["estado"])
                st.dataframe(
                    ops_df.rename(columns={"operacion": "Operación", "centro": "Centro de trabajo", "estado": "Estado"}),
                    hide_index=True, use_container_width=True,
                )

                if sincronizar:
                    por_centro_pct = resultado["por_centro_pct"]
                    hoy = str(date.today())
                    resumen_sync = []
                    for et in etapas:
                        kw_list = [k.strip().lower() for k in (et["keywords_odoo"] or "").split(",") if k.strip()]
                        centros_match = [c for c in por_centro_pct if any(kw in c.lower() for kw in kw_list)]
                        if centros_match:
                            pct_etapa = sum(por_centro_pct[c] for c in centros_match) / len(centros_match)
                        else:
                            pct_etapa = 0.0
                        piezas_equivalentes = pct_etapa * proyecto["cantidad_total"]
                        upsert_avance_etapa(conn, et["etapa_id"], piezas_equivalentes, hoy)
                        resumen_sync.append((et["etapa"], f"{pct_etapa*100:.0f}%"))
                    conn.commit()
                    st.success("Avance aplicado a las etapas según el checklist de Odoo.")
                    st.dataframe(pd.DataFrame(resumen_sync, columns=["Etapa", "% aplicado"]),
                                 hide_index=True, use_container_width=True)
                    st.rerun()
            except Exception as e:
                st.error(f"No se pudo consultar Odoo: {e}")

# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

st.divider()
st.subheader("Dashboard")

dg1, dg2 = st.columns([1, 2])
with dg1:
    st.plotly_chart(gauge(pct_total, "Avance total"), use_container_width=True)
with dg2:
    fig_bar = go.Figure(go.Bar(
        x=etapas_avance["etapa"], y=etapas_avance["pct_etapa"] * 100,
        text=[f"{v:.1f}%" for v in etapas_avance["pct_etapa"] * 100],
        textposition="outside", marker_color="#2E7BC4",
    ))
    fig_bar.update_layout(yaxis_range=[0, 100], height=220, margin=dict(l=20, r=20, t=20, b=20),
                           paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)", font_color="#E8EDF1")
    st.plotly_chart(fig_bar, use_container_width=True)

faltante = proyecto["cantidad_total"] * (1 - pct_total)
st.metric("Piezas equivalentes que faltan (ponderado)", f"{faltante:.1f} {proyecto['unidad']}")

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
