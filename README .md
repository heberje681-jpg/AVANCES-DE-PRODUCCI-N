# Avance de Producción — MARVA

App genérica en Streamlit para dar seguimiento al avance de fabricación,
sin necesidad de un Excel distinto por producto. Todo vive en **una sola
pantalla por proyecto**, como el Excel original, en vez de estar repartido
en varias páginas.

## Cómo correrla

```bash
pip install -r requirements.txt
streamlit run app.py
```

Se crea automáticamente `avance.db` (SQLite) en la misma carpeta. Ahí
viven todos los proyectos.

## Cómo está organizada la pantalla

1. **Encabezado**: pieza, cantidad a producir, responsable, % avance total.
2. **Etapas, pesos y centros de trabajo de Odoo** (colapsado): las etapas
   por default siguen el proceso real de Marva — Habilitado (corte,
   doblez, roscado, barrenado), Armado (puntear), Soldadura, Pintura
   (preparación, fondo, pintura — o solo "fondo gris" si la pieza va
   ahogada en concreto, hay un checkbox para eso al crear el proyecto).
3. **Agregar materiales** (colapsado): manual, archivo CSV/Excel, o
   traído directo de la BOM en Odoo.
4. **Sincronizar avance con Odoo** (colapsado): mete la referencia de la
   Orden de Fabricación (ej. `WH/MO/00123`) y trae cuánto ha avanzado
   cada centro de trabajo — lo reparte entre las etapas según las
   palabras clave configuradas y actualiza el avance de todos los
   materiales de esa etapa automáticamente.
5. **Tabla de materiales y avance**: una sola tabla editable, un
   renglón por material y una columna por etapa — igual que el Excel
   original. Ahí mismo agregas/quitas materiales y capturas avance.
6. **Dashboard**: gauge de avance total, barras por etapa, y cuánto
   falta por completar — en la misma pantalla, sin cambiar de página.
7. **Administrar proyecto**: archivar o eliminar, al fondo.

## Conexión a Odoo

Se conecta por xmlrpc (sin librerías extra). Dos formas de traer datos:

- **BOM de un producto** (`fetch_bom_from_odoo`): trae la lista de
  materiales de una pieza por código o nombre.
- **Avance por centro de trabajo** (`fetch_avance_por_centro`): trae,
  para una Orden de Fabricación, cuántas piezas ha completado cada
  centro de trabajo (`mrp.workorder`), y la app las reparte entre las
  etapas locales usando las palabras clave configuradas.

Guarda tus credenciales en **Settings → Secrets** de Streamlit Cloud así
(cualquiera de los dos formatos funciona):

```toml
[odoo]
url = "https://tuempresa.odoo.com"
db = "tu_base"
user = "tu_usuario"
password = "tu_password_o_api_key"
```

o en formato plano:

```toml
ODOO_URL = "https://tuempresa.odoo.com"
ODOO_DB = "tu_base"
ODOO_USER = "tu_usuario"
ODOO_PASSWORD = "tu_password_o_api_key"
```

Con eso guardado, la app detecta la conexión sola y no vuelve a pedir
usuario/contraseña.
