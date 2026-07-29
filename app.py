"""
Avance de Producción — App genérica de captura y seguimiento de avance de fabricación.

Reemplaza el esquema de "un Excel por pieza" con una sola app donde:
  1. Se crea un proyecto/pieza (cantidad a producir + etapas con peso %)
  2. Se cargan los materiales/componentes (manual, pegado o CSV/Excel)
  3. Se captura el avance por material x etapa
  4. Se ve el dashboard con % ponderado por KG (o por cantidad si no hay peso)

Ejecutar con:  streamlit run app.py
"""

import sqlite3
from datetime import date

import pandas as pd
import streamlit as st

import odoo_connector as odoo

DB_PATH = "avance.db"

# ---------------------------------------------------------------------------
# Capa de datos
# ---------------------------------------------------------------------------

def get_conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    conn = get_conn()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS proyectos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT NOT NULL,
            cantidad_total REAL NOT NULL,
            unidad TEXT DEFAULT 'pza',
            fecha TEXT,
            responsable TEXT,
            origen_bom TEXT DEFAULT 'manual',
            activo INTEGER DEFAULT 1
        );

        CREATE TABLE IF NOT EXISTS etapas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            proyecto_id INTEGER NOT NULL,
            nombre TEXT NOT NULL,
            peso REAL NOT NULL,
            orden INTEGER,
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
        """
    )
    conn.commit()
    return conn


def df_query(conn, sql, params=()):
    return pd.read_sql_query(sql, conn, params=params)


# ---------------------------------------------------------------------------
# Utilidades de cálculo
# ---------------------------------------------------------------------------

def get_avance_actual(conn, proyecto_id: int) -> pd.DataFrame:
    """Devuelve, por material x etapa, la última cantidad avanzada acumulada capturada."""
    sql = """
    SELECT m.id AS material_id, m.descripcion, m.cantidad_total, m.kg_pza,
           e.id AS etapa_id, e.nombre AS etapa, e.peso AS peso_etapa, e.orden,
           COALESCE(a.cantidad_avanzada, 0) AS cantidad_avanzada,
           a.fecha
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
    return df_query(conn, sql, (proyecto_id,))


def calcular_dashboard(df: pd.DataFrame):
    """Calcula % por etapa (ponderado por KG, o por cantidad si no hay peso) y % total."""
    if df.empty:
        return pd.DataFrame(), pd.DataFrame(), 0.0

    df = df.copy()
    df["kg_total"] = df["cantidad_total"] * df["kg_pza"]
    # base de ponderación: kg si hay peso capturado, si no, cantidad de piezas
    df["base"] = df["kg_total"].where(df["kg_pza"] > 0, df["cantidad_total"])
    df["avanzado_base"] = df["cantidad_avanzada"] * df["base"] / df["cantidad_total"].replace(0, pd.NA)
    df["avanzado_base"] = df["avanzado_base"].fillna(0)

    # % avance por etapa a nivel proyecto (ponderado)
    por_etapa = (
        df.groupby(["etapa_id", "etapa", "peso_etapa", "orden"], as_index=False)
        .agg(base_total=("base", "sum"), avanzado_total=("avanzado_base", "sum"))
    )
    por_etapa["pct_etapa"] = (por_etapa["avanzado_total"] / por_etapa["base_total"].replace(0, pd.NA)).fillna(0)
    por_etapa = por_etapa.sort_values("orden")

    pct_total = float((por_etapa["pct_etapa"] * por_etapa["peso_etapa"]).sum())

    # % avance por material (ponderado por las etapas de ese material)
    por_material = df.copy()
    por_material["pct_material_etapa"] = (
        por_material["cantidad_avanzada"] / por_material["cantidad_total"].replace(0, pd.NA)
    ).fillna(0)
    por_material["pct_pond"] = por_material["pct_material_etapa"] * por_material["peso_etapa"]
    resumen_material = (
        por_material.groupby(["material_id", "descripcion", "cantidad_total", "kg_pza"], as_index=False)
        .agg(pct_avance=("pct_pond", "sum"))
    )

    return por_etapa, resumen_material, pct_total


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------

st.set_page_config(page_title="MARVA · Avance de Producción", layout="wide", page_icon="⚙️")
conn = init_db()

# ---------------------------------------------------------------------------
# Identidad visual Marva: grafito + acero + naranja de seguridad
# ---------------------------------------------------------------------------
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Barlow+Condensed:wght@500;600;700&family=Inter:wght@400;500;600&display=swap');

:root {
    --marva-bg: #14181C;
    --marva-panel: #1D242B;
    --marva-panel-2: #232B33;
    --marva-steel: #2E86AB;
    --marva-orange: #F26430;
    --marva-text: #E8EDF1;
    --marva-muted: #8C99A6;
    --marva-border: #2C363F;
}

html, body, [class*="css"] { font-family: 'Inter', sans-serif; }
h1, h2, h3, .marva-plate, .marva-plate * { font-family: 'Barlow Condensed', sans-serif; }

.stApp { background-color: var(--marva-bg); }
section[data-testid="stSidebar"] { background-color: var(--marva-panel); border-right: 1px solid var(--marva-border); }

/* Placa tipo nameplate industrial */
.marva-plate {
    background: linear-gradient(120deg, #1A2027 0%, #232B33 60%, #1A2027 100%);
    border: 1px solid var(--marva-border);
    border-left: 6px solid var(--marva-orange);
    border-radius: 6px;
    padding: 18px 26px;
    margin-bottom: 22px;
    display: flex;
    align-items: center;
    justify-content: space-between;
}
.marva-plate .brand { display: flex; align-items: baseline; gap: 14px; }
.marva-plate .brand-name {
    font-weight: 700; font-size: 30px; letter-spacing: 2px; color: var(--marva-text);
}
.marva-plate .brand-tag {
    font-size: 15px; letter-spacing: 3px; text-transform: uppercase; color: var(--marva-orange);
}
.marva-plate .brand-sub { font-family: 'Inter'; font-size: 13px; color: var(--marva-muted); margin-top: 2px; }

/* Metrics como tarjetas de acero */
div[data-testid="stMetric"] {
    background-color: var(--marva-panel-2);
    border: 1px solid var(--marva-border);
    border-radius: 6px;
    padding: 12px 16px 10px 16px;
    border-top: 3px solid var(--marva-steel);
}
div[data-testid="stMetricLabel"] { color: var(--marva-muted) !important; text-transform: uppercase; letter-spacing: 1px; font-size: 12px !important; }
div[data-testid="stMetricValue"] { color: var(--marva-text) !important; font-family: 'Barlow Condensed'; }

/* Botones */
.stButton > button {
    background-color: var(--marva-steel);
    color: white;
    border: none;
    border-radius: 5px;
    font-weight: 600;
    letter-spacing: 0.3px;
}
.stButton > button:hover { background-color: #256E8C; color: white; }
.stFormSubmitButton > button { background-color: var(--marva-orange); color: white; border: none; font-weight: 600; }
.stFormSubmitButton > button:hover { background-color: #D6541F; color: white; }

/* Expanders / tabs */
div[data-testid="stExpander"] { background-color: var(--marva-panel-2); border: 1px solid var(--marva-border); border-radius: 6px; }
button[data-baseweb="tab"] { font-family: 'Barlow Condensed'; font-size: 16px; letter-spacing: 0.5px; }

/* Barras de progreso -> acento naranja */
div[data-testid="stProgress"] > div > div > div { background-color: var(--marva-orange) !important; }

hr { border-color: var(--marva-border); }
</style>
""", unsafe_allow_html=True)

st.markdown("""
<div class="marva-plate">
    <div class="brand">
        <span class="brand-name">MARVA</span>
        <span class="brand-tag">Producción</span>
    </div>
    <div class="brand-sub">Seguimiento de avance de fabricación · piso de planta</div>
</div>
""", unsafe_allow_html=True)

st.sidebar.markdown("### ⚙️ Avance de Producción")
seccion = st.sidebar.radio(
    "Ir a:",
    ["Proyectos activos", "Nuevo proyecto", "Materiales", "Captura de avance", "Dashboard"],
)

proyectos_df = df_query(conn, "SELECT * FROM proyectos WHERE activo = 1 ORDER BY id DESC")

# ---------------------------------------------------------------------------
# Proyectos activos
# ---------------------------------------------------------------------------
if seccion == "Proyectos activos":
    st.header("Proyectos activos")
    if proyectos_df.empty:
        st.info("Todavía no hay proyectos. Ve a 'Nuevo proyecto' para crear el primero.")
    else:
        filas = []
        for _, p in proyectos_df.iterrows():
            df_av = get_avance_actual(conn, p["id"])
            _, _, pct_total = calcular_dashboard(df_av)
            filas.append(
                {
                    "Proyecto": p["nombre"],
                    "Cantidad a producir": f"{p['cantidad_total']:g} {p['unidad']}",
                    "Responsable": p["responsable"] or "—",
                    "% Avance total": pct_total,
                }
            )
        resumen = pd.DataFrame(filas)
        st.dataframe(
            resumen,
            column_config={
                "% Avance total": st.column_config.ProgressColumn(
                    "% Avance total", min_value=0, max_value=1, format="%.1f%%"
                )
            },
            use_container_width=True,
            hide_index=True,
        )

        st.divider()
        st.subheader("Administrar proyectos")
        st.caption(
            "**Archivar** lo saca de la lista de activos pero no borra nada (lo puedes recuperar). "
            "**Eliminar** lo borra por completo, junto con sus materiales y avances — no se puede deshacer."
        )
        proyecto_admin = st.selectbox(
            "Proyecto a administrar",
            proyectos_df["id"],
            format_func=lambda x: proyectos_df.set_index("id").loc[x, "nombre"],
            key="proyecto_admin",
        )
        nombre_admin = proyectos_df.set_index("id").loc[proyecto_admin, "nombre"]

        ac1, ac2 = st.columns(2)
        with ac1:
            if st.button("🗄️ Archivar proyecto"):
                conn.execute("UPDATE proyectos SET activo = 0 WHERE id = ?", (int(proyecto_admin),))
                conn.commit()
                st.success(f"'{nombre_admin}' archivado.")
                st.rerun()

        with ac2:
            confirmar = st.checkbox(f"Confirmo que quiero eliminar '{nombre_admin}' definitivamente")
            if st.button("🗑️ Eliminar definitivamente", disabled=not confirmar):
                conn.execute("DELETE FROM proyectos WHERE id = ?", (int(proyecto_admin),))
                conn.commit()
                st.success(f"'{nombre_admin}' eliminado.")
                st.rerun()

# ---------------------------------------------------------------------------
# Nuevo proyecto
# ---------------------------------------------------------------------------
elif seccion == "Nuevo proyecto":
    st.header("Nuevo proyecto / pieza")

    with st.form("form_proyecto"):
        col1, col2 = st.columns(2)
        with col1:
            nombre = st.text_input("Nombre de la pieza / proyecto *")
            cantidad_total = st.number_input("Cantidad a producir *", min_value=0.0, step=1.0)
            unidad = st.text_input("Unidad", value="pza")
        with col2:
            fecha_proy = st.date_input("Fecha", value=date.today())
            responsable = st.text_input("Responsable")

        st.markdown("**Etapas de producción y su peso en el avance total**")
        st.caption("Deja los defaults o edita nombres/pesos. La suma de pesos debe dar 1.0 (100%).")
        etapas_default = pd.DataFrame(
            [
                {"nombre": "Habilitado", "peso": 0.4},
                {"nombre": "Armado", "peso": 0.2},
                {"nombre": "Resoldado", "peso": 0.4},
            ]
        )
        etapas_edit = st.data_editor(
            etapas_default, num_rows="dynamic", use_container_width=True, key="etapas_editor"
        )

        submitted = st.form_submit_button("Crear proyecto")

        if submitted:
            suma_pesos = etapas_edit["peso"].sum()
            if not nombre or cantidad_total <= 0:
                st.error("Falta nombre o cantidad a producir.")
            elif abs(suma_pesos - 1.0) > 0.01:
                st.error(f"La suma de los pesos de las etapas es {suma_pesos:.2f}, debe ser 1.0.")
            else:
                cur = conn.execute(
                    "INSERT INTO proyectos (nombre, cantidad_total, unidad, fecha, responsable) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (nombre, cantidad_total, unidad, str(fecha_proy), responsable),
                )
                proyecto_id = cur.lastrowid
                for i, row in etapas_edit.reset_index(drop=True).iterrows():
                    conn.execute(
                        "INSERT INTO etapas (proyecto_id, nombre, peso, orden) VALUES (?, ?, ?, ?)",
                        (proyecto_id, row["nombre"], row["peso"], i),
                    )
                conn.commit()
                st.success(f"Proyecto '{nombre}' creado. Ahora ve a 'Materiales' para agregar los componentes.")

# ---------------------------------------------------------------------------
# Materiales
# ---------------------------------------------------------------------------
elif seccion == "Materiales":
    st.header("Materiales / componentes")

    if proyectos_df.empty:
        st.info("Primero crea un proyecto en 'Nuevo proyecto'.")
    else:
        proyecto_sel = st.selectbox(
            "Proyecto", proyectos_df["id"], format_func=lambda x: proyectos_df.set_index("id").loc[x, "nombre"]
        )

        with st.expander("🔌 Traer BOM de Odoo", expanded=odoo.is_odoo_configured()):
            if odoo.is_odoo_configured():
                st.success("Odoo conectado (credenciales guardadas en Secrets). Solo mete el código de la pieza.")
                odoo_url, odoo_db, odoo_user, odoo_pass = odoo.get_credentials_from_secrets()

                codigo_producto = st.text_input("Código o nombre del producto en Odoo", key="codigo_prod_secret")
                if st.button("Traer BOM de Odoo", key="btn_odoo_secret"):
                    if not codigo_producto:
                        st.error("Falta el código del producto.")
                    else:
                        try:
                            materiales_odoo = odoo.fetch_bom_from_odoo(
                                odoo_url, odoo_db, odoo_user, odoo_pass, codigo_producto
                            )
                            for mat in materiales_odoo:
                                conn.execute(
                                    "INSERT INTO materiales (proyecto_id, descripcion, cantidad_total, kg_pza) "
                                    "VALUES (?, ?, ?, ?)",
                                    (proyecto_sel, mat["descripcion"], mat["cantidad_total"], mat["kg_pza"]),
                                )
                            conn.commit()
                            st.success(f"Se importaron {len(materiales_odoo)} materiales desde Odoo.")
                            st.rerun()
                        except Exception as e:
                            st.error(f"No se pudo traer la BOM: {e}")
            else:
                st.caption(
                    "Mete tus datos de conexión de Odoo (se quedan solo en esta sesión, no se guardan "
                    "en ningún archivo) y trae la BOM del producto directo, sin capturarla a mano. "
                    "Tip: si guardas estos mismos datos en Settings > Secrets de Streamlit Cloud "
                    "(sección [odoo]), no te los vuelve a pedir."
                )
                oc1, oc2 = st.columns(2)
                odoo_url = oc1.text_input("URL de Odoo", value=st.session_state.get("odoo_url", ""),
                                           placeholder="https://tuempresa.odoo.com")
                odoo_db = oc2.text_input("Base de datos", value=st.session_state.get("odoo_db", ""))
                oc3, oc4 = st.columns(2)
                odoo_user = oc3.text_input("Usuario", value=st.session_state.get("odoo_user", ""))
                odoo_pass = oc4.text_input("Contraseña / API key", type="password")

                if st.button("Probar conexión"):
                    if odoo_url and odoo_db and odoo_user and odoo_pass:
                        ok, msg = odoo.probar_conexion(odoo_url, odoo_db, odoo_user, odoo_pass)
                        st.session_state["odoo_url"] = odoo_url
                        st.session_state["odoo_db"] = odoo_db
                        st.session_state["odoo_user"] = odoo_user
                        (st.success if ok else st.error)(msg)
                    else:
                        st.error("Faltan datos de conexión.")

                codigo_producto = st.text_input("Código o nombre del producto en Odoo")
                if st.button("Traer BOM de Odoo"):
                    if not (odoo_url and odoo_db and odoo_user and odoo_pass and codigo_producto):
                        st.error("Faltan datos de conexión o el código del producto.")
                    else:
                        try:
                            materiales_odoo = odoo.fetch_bom_from_odoo(
                                odoo_url, odoo_db, odoo_user, odoo_pass, codigo_producto
                            )
                            for mat in materiales_odoo:
                                conn.execute(
                                    "INSERT INTO materiales (proyecto_id, descripcion, cantidad_total, kg_pza) "
                                    "VALUES (?, ?, ?, ?)",
                                    (proyecto_sel, mat["descripcion"], mat["cantidad_total"], mat["kg_pza"]),
                                )
                            conn.commit()
                            st.success(f"Se importaron {len(materiales_odoo)} materiales desde Odoo.")
                            st.rerun()
                        except Exception as e:
                            st.error(f"No se pudo traer la BOM: {e}")

        st.markdown("**Cargar por archivo (CSV o Excel)**")
        st.caption("Columnas esperadas: descripcion, cantidad_total, kg_pza (kg_pza es opcional)")
        archivo = st.file_uploader("Subir archivo", type=["csv", "xlsx"])
        if archivo is not None:
            if archivo.name.endswith(".csv"):
                df_carga = pd.read_csv(archivo)
            else:
                df_carga = pd.read_excel(archivo)
            df_carga.columns = [c.strip().lower() for c in df_carga.columns]
            if "kg_pza" not in df_carga.columns:
                df_carga["kg_pza"] = 0
            st.dataframe(df_carga, use_container_width=True)
            if st.button("Importar estos materiales"):
                for _, row in df_carga.iterrows():
                    conn.execute(
                        "INSERT INTO materiales (proyecto_id, descripcion, cantidad_total, kg_pza) "
                        "VALUES (?, ?, ?, ?)",
                        (proyecto_sel, row["descripcion"], row["cantidad_total"], row.get("kg_pza", 0)),
                    )
                conn.commit()
                st.success(f"Se importaron {len(df_carga)} materiales.")
                st.rerun()

        st.divider()
        st.markdown("**Materiales actuales del proyecto (edita directo en la tabla)**")
        mat_df = df_query(
            conn,
            "SELECT id, descripcion, cantidad_total, kg_pza FROM materiales WHERE proyecto_id = ? ORDER BY id",
            (proyecto_sel,),
        )
        mat_editada = st.data_editor(
            mat_df, num_rows="dynamic", use_container_width=True, key="materiales_editor", disabled=["id"]
        )

        if st.button("Guardar cambios de materiales"):
            ids_originales = set(mat_df["id"]) if not mat_df.empty else set()
            ids_editados = set(mat_editada["id"].dropna()) if not mat_editada.empty else set()

            # eliminar los que ya no están
            for mid in ids_originales - ids_editados:
                conn.execute("DELETE FROM materiales WHERE id = ?", (int(mid),))

            # actualizar / insertar
            for _, row in mat_editada.iterrows():
                if pd.isna(row.get("id")):
                    conn.execute(
                        "INSERT INTO materiales (proyecto_id, descripcion, cantidad_total, kg_pza) "
                        "VALUES (?, ?, ?, ?)",
                        (proyecto_sel, row["descripcion"], row["cantidad_total"], row["kg_pza"]),
                    )
                else:
                    conn.execute(
                        "UPDATE materiales SET descripcion = ?, cantidad_total = ?, kg_pza = ? WHERE id = ?",
                        (row["descripcion"], row["cantidad_total"], row["kg_pza"], int(row["id"])),
                    )
            conn.commit()
            st.success("Materiales actualizados.")
            st.rerun()

# ---------------------------------------------------------------------------
# Captura de avance
# ---------------------------------------------------------------------------
elif seccion == "Captura de avance":
    st.header("Captura de avance")

    if proyectos_df.empty:
        st.info("Primero crea un proyecto en 'Nuevo proyecto'.")
    else:
        proyecto_sel = st.selectbox(
            "Proyecto",
            proyectos_df["id"],
            format_func=lambda x: proyectos_df.set_index("id").loc[x, "nombre"],
            key="captura_proyecto",
        )
        fecha_captura = st.date_input("Fecha de captura", value=date.today(), key="fecha_captura")

        df_actual = get_avance_actual(conn, proyecto_sel)
        if df_actual.empty:
            st.warning("Este proyecto todavía no tiene materiales ni etapas. Agrégalos primero.")
        else:
            st.caption(
                "Captura la cantidad **acumulada** avanzada a la fecha por material y etapa "
                "(no el incremento del día)."
            )
            pivot = df_actual.pivot_table(
                index=["material_id", "descripcion", "cantidad_total"],
                columns="etapa",
                values="cantidad_avanzada",
                aggfunc="first",
            ).reset_index()

            pivot_editado = st.data_editor(
                pivot, use_container_width=True, key="captura_editor", disabled=["material_id", "descripcion", "cantidad_total"]
            )

            if st.button("Guardar avance"):
                etapas_proy = df_query(
                    conn, "SELECT id, nombre FROM etapas WHERE proyecto_id = ?", (proyecto_sel,)
                )
                nombre_a_id = dict(zip(etapas_proy["nombre"], etapas_proy["id"]))

                for _, row in pivot_editado.iterrows():
                    for etapa_nombre, etapa_id in nombre_a_id.items():
                        valor = row.get(etapa_nombre)
                        if pd.isna(valor):
                            continue
                        conn.execute(
                            "INSERT INTO avances (material_id, etapa_id, cantidad_avanzada, fecha) "
                            "VALUES (?, ?, ?, ?)",
                            (int(row["material_id"]), int(etapa_id), float(valor), str(fecha_captura)),
                        )
                conn.commit()
                st.success("Avance guardado.")
                st.rerun()

# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------
elif seccion == "Dashboard":
    st.header("Dashboard de avance")

    if proyectos_df.empty:
        st.info("Primero crea un proyecto en 'Nuevo proyecto'.")
    else:
        proyecto_sel = st.selectbox(
            "Proyecto",
            proyectos_df["id"],
            format_func=lambda x: proyectos_df.set_index("id").loc[x, "nombre"],
            key="dash_proyecto",
        )
        info_proy = proyectos_df.set_index("id").loc[proyecto_sel]

        df_actual = get_avance_actual(conn, proyecto_sel)
        por_etapa, por_material, pct_total = calcular_dashboard(df_actual)

        c1, c2, c3 = st.columns(3)
        c1.metric("Cantidad a producir", f"{info_proy['cantidad_total']:g} {info_proy['unidad']}")
        c2.metric("Responsable", info_proy["responsable"] or "—")
        c3.metric("% Avance total ponderado", f"{pct_total * 100:.1f}%")

        st.progress(min(max(pct_total, 0.0), 1.0))

        st.subheader("Avance por etapa")
        if not por_etapa.empty:
            st.dataframe(
                por_etapa[["etapa", "peso_etapa", "pct_etapa"]].rename(
                    columns={"etapa": "Etapa", "peso_etapa": "Peso en avance total", "pct_etapa": "% Avance"}
                ),
                column_config={
                    "% Avance": st.column_config.ProgressColumn("% Avance", min_value=0, max_value=1, format="%.1f%%"),
                    "Peso en avance total": st.column_config.NumberColumn(format="%.0f%%"),
                },
                use_container_width=True,
                hide_index=True,
            )

        st.subheader("% de avance por material")
        if not por_material.empty:
            st.dataframe(
                por_material[["descripcion", "cantidad_total", "kg_pza", "pct_avance"]].rename(
                    columns={
                        "descripcion": "Material",
                        "cantidad_total": "Cantidad total",
                        "kg_pza": "Kg/pza",
                        "pct_avance": "% Avance",
                    }
                ),
                column_config={
                    "% Avance": st.column_config.ProgressColumn("% Avance", min_value=0, max_value=1, format="%.1f%%")
                },
                use_container_width=True,
                hide_index=True,
            )

        st.subheader("Tendencia de avance total en el tiempo")
        hist_sql = """
        SELECT a.fecha, m.id AS material_id, m.cantidad_total, m.kg_pza,
               e.id AS etapa_id, e.peso AS peso_etapa, a.cantidad_avanzada
        FROM avances a
        JOIN materiales m ON m.id = a.material_id
        JOIN etapas e ON e.id = a.etapa_id
        WHERE m.proyecto_id = ?
        ORDER BY a.fecha
        """
        hist = df_query(conn, hist_sql, (proyecto_sel,))
        if not hist.empty:
            hist["base"] = (hist["cantidad_total"] * hist["kg_pza"]).where(
                hist["kg_pza"] > 0, hist["cantidad_total"]
            )
            hist["pct_material"] = (hist["cantidad_avanzada"] / hist["cantidad_total"].replace(0, pd.NA)).fillna(0)
            hist["pct_pond"] = hist["pct_material"] * hist["peso_etapa"]
            # % total por fecha: para cada fecha, usar la última captura conocida de cada material/etapa hasta esa fecha
            fechas = sorted(hist["fecha"].unique())
            serie = []
            for f in fechas:
                snap = hist[hist["fecha"] <= f].sort_values("fecha").groupby(["material_id", "etapa_id"]).last().reset_index()
                por_etapa_f = snap.groupby("etapa_id").apply(
                    lambda g: (g["cantidad_avanzada"] * g["base"] / g["cantidad_total"].replace(0, pd.NA)).fillna(0).sum()
                    / g["base"].sum() if g["base"].sum() > 0 else 0
                ).reset_index(name="pct_etapa")
                pesos = snap.drop_duplicates("etapa_id")[["etapa_id", "peso_etapa"]]
                merged = por_etapa_f.merge(pesos, on="etapa_id")
                pct_f = (merged["pct_etapa"] * merged["peso_etapa"]).sum()
                serie.append({"fecha": f, "% Avance total": pct_f})
            serie_df = pd.DataFrame(serie).set_index("fecha")
            st.line_chart(serie_df)
        else:
            st.caption("Todavía no hay historial de capturas para graficar.")

st.sidebar.divider()
st.sidebar.caption("Base de datos local: avance.db (SQLite). Un solo archivo para todos los proyectos.")
