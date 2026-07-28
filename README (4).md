# Avance de Producción

App genérica en Streamlit para dar seguimiento al avance de fabricación de
cualquier pieza/proyecto, sin necesidad de un Excel distinto por producto.

## Cómo correrla

```bash
pip install -r requirements.txt
streamlit run app.py
```

Se crea automáticamente un archivo `avance.db` (SQLite) en la misma carpeta.
Ahí viven todos los proyectos — no hay que crear un archivo nuevo por pieza.

## Flujo de uso

1. **Nuevo proyecto**: nombre de la pieza, cantidad a producir, y las etapas
   con su peso % (viene precargado Habilitado 40% / Armado 20% / Resoldado
   40%, pero se puede editar o agregar/quitar etapas según la pieza).
2. **Materiales**: agrega los componentes — a mano, pegando/subiendo un
   Excel/CSV (columnas: `descripcion`, `cantidad_total`, `kg_pza`), o a
   futuro directo desde Odoo (ver más abajo).
3. **Captura de avance**: capturas la cantidad *acumulada* avanzada por
   material y etapa. Se guarda con fecha, así que también queda historial.
4. **Dashboard**: % de avance total ponderado (igual que el Excel: pondera
   por KG cuando hay `kg_pza` capturado, o por cantidad de piezas si no),
   % por etapa, % por material, y la tendencia en el tiempo.

## Conexión a Odoo

Ya está conectada de verdad (vía xmlrpc, sin librerías extra). En la
pestaña de Materiales, abre "🔌 Traer BOM de Odoo", mete:

- URL de tu Odoo (ej. `https://tuempresa.odoo.com`)
- Base de datos
- Usuario y contraseña (o API key)
- Código del producto (`default_code` en Odoo)

Dale "Probar conexión" primero para confirmar que las credenciales
sirven, y luego "Traer BOM de Odoo" para importar los materiales de esa
pieza directo, sin capturarlos a mano.

Esas credenciales solo viven en la sesión del navegador mientras usas la
app — no se guardan en ningún archivo ni se suben a git. Si subes la app
a Streamlit Community Cloud, cada quien las captura ahí cuando la usa.

Si en algún momento prefieres no estar tecleando las credenciales cada
vez, `.streamlit/secrets.toml.example` trae la plantilla para guardarlas
como secreto del proyecto (opcional, no es necesario para que funcione).
