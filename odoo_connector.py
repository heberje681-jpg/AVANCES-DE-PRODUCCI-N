"""
Conexion a Odoo para traer la BOM de una pieza directo, en vez de
capturarla a mano.

Usa xmlrpc, que es la forma estandar de conectarse a Odoo sin instalar
ninguna libreria extra (viene incluida en Python).
"""
import re
import xmlrpc.client


def _parse_codigo_y_nombre(texto: str):
    """Si el texto viene como Odoo lo muestra ('[ACST35] ANCLA DE 1...'),
    separa el código de corchetes del nombre. Si no trae corchetes, regresa
    el texto completo como posible código Y como posible nombre."""
    m = re.match(r"^\s*\[([^\]]+)\]\s*(.*)$", texto)
    if m:
        codigo = m.group(1).strip()
        nombre = m.group(2).strip()
        return codigo, nombre
    return texto.strip(), texto.strip()


def get_credentials_from_secrets():
    """Si hay credenciales guardadas en .streamlit/secrets.toml (local) o en
    Settings > Secrets de Streamlit Cloud, las regresa. Acepta dos formatos:

        [odoo]                          o          ODOO_URL = "..."
        url = "..."                                 ODOO_DB = "..."
        db = "..."                                   ODOO_USER = "..."
        user = "..."                                 ODOO_PASSWORD = "..."
        password = "..."

    Si no hay nada guardado, regresa 4 None y la app cae de vuelta a
    pedirlas a mano."""
    try:
        import streamlit as st

        if "odoo" in st.secrets:
            o = st.secrets["odoo"]
            return o.get("url"), o.get("db"), o.get("user"), o.get("password")

        if "ODOO_URL" in st.secrets:
            return (
                st.secrets.get("ODOO_URL"),
                st.secrets.get("ODOO_DB"),
                st.secrets.get("ODOO_USER"),
                st.secrets.get("ODOO_PASSWORD"),
            )
    except Exception:
        pass
    return None, None, None, None


def is_odoo_configured() -> bool:
    url, db, user, password = get_credentials_from_secrets()
    return all([url, db, user, password])


def probar_conexion(url: str, db: str, user: str, password: str):
    """Regresa (ok: bool, mensaje: str). No lanza excepcion."""
    try:
        common = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common")
        uid = common.authenticate(db, user, password, {})
        if not uid:
            return False, "No se pudo autenticar. Revisa usuario/contraseña/base de datos."
        return True, f"Conectado correctamente (uid {uid})."
    except Exception as e:
        return False, f"Error al conectar: {e}"


def fetch_bom_from_odoo(url: str, db: str, user: str, password: str, codigo_producto: str):
    """Trae la BOM del producto desde Odoo. Primero busca por código exacto
    (default_code, ej. 'ACST35'); si no encuentra nada, busca por nombre
    (para cuando se pega el nombre completo de la pieza en vez del código).

    Regresa una lista de dicts: [{"descripcion", "cantidad_total", "kg_pza"}, ...]
    Lanza excepcion con un mensaje claro si algo falla, para mostrarlo en la UI.
    """
    common = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common")
    uid = common.authenticate(db, user, password, {})
    if not uid:
        raise ValueError("No se pudo autenticar en Odoo. Revisa las credenciales.")

    models = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object")

    codigo, nombre = _parse_codigo_y_nombre(codigo_producto)
    ctx = {"active_test": False}  # incluye productos archivados/inactivos en la búsqueda

    # 1) intento por código exacto (lo que aparece entre corchetes en Odoo, ej. ACST35)
    productos = models.execute_kw(
        db, uid, password, "product.product", "search_read",
        [[["default_code", "=", codigo]]],
        {"fields": ["id", "name", "default_code", "product_tmpl_id"], "limit": 5, "context": ctx},
    )

    # 2) si no hubo match exacto por código, busca por nombre (contiene el texto)
    if not productos:
        productos = models.execute_kw(
            db, uid, password, "product.product", "search_read",
            [[["name", "ilike", nombre]]],
            {"fields": ["id", "name", "default_code", "product_tmpl_id"], "limit": 5, "context": ctx},
        )

    # 3) ultimo intento: solo la palabra mas distintiva del nombre (la mas larga),
    # por si hay diferencias de acentos/espacios en el resto del texto
    if not productos and nombre:
        palabra_clave = max(nombre.split(), key=len)
        productos = models.execute_kw(
            db, uid, password, "product.product", "search_read",
            [[["name", "ilike", palabra_clave]]],
            {"fields": ["id", "name", "default_code", "product_tmpl_id"], "limit": 5, "context": ctx},
        )

    if not productos:
        raise ValueError(
            f"No se encontró ningún producto con código o nombre '{codigo_producto}' (probé incluso con "
            "productos archivados). Puede que el producto no exista con ese código exacto en Odoo — "
            "verifícalo abriendo la ficha del producto ahí y confirmando el código entre corchetes."
        )

    if len(productos) > 1:
        opciones = ", ".join(f"{p.get('default_code') or '—'} ({p['name']})" for p in productos)
        raise ValueError(
            f"Encontré {len(productos)} productos que hacen match: {opciones}. "
            "Usa el código exacto (entre corchetes en Odoo) para no ambigüedad."
        )

    tmpl_id = productos[0]["product_tmpl_id"][0]

    boms = models.execute_kw(
        db, uid, password, "mrp.bom", "search_read",
        [[["product_tmpl_id", "=", tmpl_id]]],
        {"fields": ["id"], "limit": 1},
    )
    if not boms:
        raise ValueError(f"El producto '{productos[0]['name']}' no tiene BOM cargada en Odoo.")

    lineas = models.execute_kw(
        db, uid, password, "mrp.bom.line", "search_read",
        [[["bom_id", "=", boms[0]["id"]]]],
        {"fields": ["product_id", "product_qty", "product_uom_id"]},
    )

    materiales = []
    for linea in lineas:
        nombre_producto = linea["product_id"][1] if linea.get("product_id") else "Sin nombre"
        # Odoo no siempre trae el peso por pieza en la linea de BOM;
        # si tu catalogo lo maneja en otro campo (p.ej. weight en
        # product.template), se puede jalar aparte y cruzarlo aqui.
        materiales.append({
            "descripcion": nombre_producto,
            "cantidad_total": linea["product_qty"],
            "kg_pza": 0.0,
        })

    return materiales


def fetch_avance_por_centro(url: str, db: str, user: str, password: str, orden_referencia: str):
    """Trae el avance real de una Orden de Fabricacion (mrp.production) en Odoo,
    agrupado por centro de trabajo (mrp.workorder.workcenter_id).

    orden_referencia acepta lo mismo que fetch_bom_from_odoo: el nombre exacto
    de la OF (ej. 'WH/MO/00123'), el codigo del producto, o el texto tal cual
    lo copias de Odoo ('[ACST35] Nombre completo'). Si no hay match exacto por
    nombre de OF, busca la orden mas reciente (no cancelada) de ese producto.

    Regresa: {"orden": str, "cantidad_total": float, "por_centro": {"Corte": 12.0, ...}}
    Lanza excepcion con mensaje claro si algo falla.
    """
    common = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common")
    uid = common.authenticate(db, user, password, {})
    if not uid:
        raise ValueError("No se pudo autenticar en Odoo. Revisa las credenciales.")

    models = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object")

    codigo, nombre = _parse_codigo_y_nombre(orden_referencia)

    # 1) buscar la orden de fabricacion por referencia exacta (ej. 'WH/MO/00123')
    ordenes = models.execute_kw(
        db, uid, password, "mrp.production", "search_read",
        [[["name", "=", orden_referencia]]],
        {"fields": ["id", "name", "product_qty", "state"], "limit": 1},
    )

    # 2) si no, intenta por codigo de producto (la mas reciente que no este cancelada)
    if not ordenes:
        ordenes = models.execute_kw(
            db, uid, password, "mrp.production", "search_read",
            [[["product_id.default_code", "=", codigo], ["state", "!=", "cancel"]]],
            {"fields": ["id", "name", "product_qty", "state"], "order": "id desc", "limit": 1},
        )

    # 3) si tampoco, intenta por nombre de producto (la mas reciente)
    if not ordenes:
        ordenes = models.execute_kw(
            db, uid, password, "mrp.production", "search_read",
            [[["product_id.name", "ilike", nombre], ["state", "!=", "cancel"]]],
            {"fields": ["id", "name", "product_qty", "state"], "order": "id desc", "limit": 1},
        )

    if not ordenes:
        raise ValueError(
            f"No se encontró ninguna orden de fabricación con referencia, código o producto '{orden_referencia}'."
        )

    orden = ordenes[0]

    workorders = models.execute_kw(
        db, uid, password, "mrp.workorder", "search_read",
        [[["production_id", "=", orden["id"]]]],
        {"fields": ["name", "workcenter_id", "state"], "order": "id asc"},
    )
    if not workorders:
        raise ValueError(
            f"La orden '{orden['name']}' no tiene operaciones/centros de trabajo capturados en Odoo."
        )

    # Marva no captura cantidades por operacion, solo si ya se hizo o no (state).
    # Como Odoo no deja pasar a la siguiente estacion sin terminar la anterior,
    # el estado 'done' de una operacion equivale a esa estacion completa para
    # TODA la orden (todas las piezas de ese lote).
    operaciones = [
        {
            "operacion": wo.get("name") or "Sin nombre",
            "centro": wo["workcenter_id"][1] if wo.get("workcenter_id") else "Sin centro",
            "estado": wo.get("state"),
            "hecha": wo.get("state") == "done",
        }
        for wo in workorders
    ]

    return {
        "orden": orden["name"],
        "cantidad_total": orden["product_qty"],
        "operaciones": operaciones,
    }


def fetch_ordenes_abiertas(url: str, db: str, user: str, password: str, limite: int = 25):
    """Trae las Ordenes de Fabricacion que siguen ABIERTAS (ni terminadas ni
    canceladas), para poder crear un proyecto en la app con un click en vez
    de capturar todo a mano.

    Regresa una lista de dicts: [{"orden", "producto", "cantidad", "estado"}, ...]
    ordenada de la mas reciente a la mas vieja.
    """
    common = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common")
    uid = common.authenticate(db, user, password, {})
    if not uid:
        raise ValueError("No se pudo autenticar en Odoo. Revisa las credenciales.")

    models = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object")

    ordenes = models.execute_kw(
        db, uid, password, "mrp.production", "search_read",
        [[["state", "not in", ["done", "cancel"]]]],
        {"fields": ["name", "product_id", "product_qty", "state"], "order": "id desc", "limit": limite},
    )

    ESTADOS = {
        "draft": "Borrador", "confirmed": "Confirmada", "planned": "Planeada",
        "progress": "En proceso", "to_close": "Por cerrar", "done": "Terminada",
    }

    return [
        {
            "orden": o["name"],
            "producto": o["product_id"][1] if o.get("product_id") else "Sin producto",
            "cantidad": o["product_qty"],
            "estado": ESTADOS.get(o.get("state"), o.get("state")),
        }
        for o in ordenes
    ]
