"""
Conexion a Odoo para traer la BOM de una pieza directo, en vez de
capturarla a mano.

Usa xmlrpc, que es la forma estandar de conectarse a Odoo sin instalar
ninguna libreria extra (viene incluida en Python).
"""
import xmlrpc.client


def get_credentials_from_secrets():
    """Si hay credenciales guardadas en .streamlit/secrets.toml (local) o en
    Settings > Secrets de Streamlit Cloud, las regresa. Si no hay, regresa
    4 None y la app cae de vuelta a pedirlas a mano."""
    try:
        import streamlit as st
        if "odoo" in st.secrets:
            o = st.secrets["odoo"]
            return o.get("url"), o.get("db"), o.get("user"), o.get("password")
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
    """Trae la BOM del producto (buscado por default_code) desde Odoo.

    Regresa una lista de dicts: [{"descripcion", "cantidad_total", "kg_pza"}, ...]
    Lanza excepcion con un mensaje claro si algo falla, para mostrarlo en la UI.
    """
    common = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common")
    uid = common.authenticate(db, user, password, {})
    if not uid:
        raise ValueError("No se pudo autenticar en Odoo. Revisa las credenciales.")

    models = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object")

    productos = models.execute_kw(
        db, uid, password, "product.product", "search_read",
        [[["default_code", "=", codigo_producto]]],
        {"fields": ["id", "name", "product_tmpl_id"], "limit": 1},
    )
    if not productos:
        raise ValueError(f"No se encontro ningun producto con codigo '{codigo_producto}'.")

    tmpl_id = productos[0]["product_tmpl_id"][0]

    boms = models.execute_kw(
        db, uid, password, "mrp.bom", "search_read",
        [[["product_tmpl_id", "=", tmpl_id]]],
        {"fields": ["id"], "limit": 1},
    )
    if not boms:
        raise ValueError(f"El producto '{codigo_producto}' no tiene BOM cargada en Odoo.")

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
