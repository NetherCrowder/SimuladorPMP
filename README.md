# Simulador PMP

Prototipo de práctica de exámenes con Python, Flask y PostgreSQL. La aplicación requiere una conexión a PostgreSQL; no crea ni usa una base de datos local.

## Crear la base y ejecutar la aplicación

Primero, crea la base de datos en tu servidor PostgreSQL, por ejemplo desde `psql` conectado con un usuario autorizado:

```sql
CREATE DATABASE simulador_pmp;
```

El esquema actualizado usa `UUID` (el equivalente de PostgreSQL a `UNIQUEIDENTIFIER`) para las cuentas. Debe instalarse en una base nueva y vacía; no ejecutes esta versión sobre la base anterior con IDs `SERIAL`. Si necesitas conservar datos de esa base, haz una copia antes de decidir cómo trasladarlos.

Desde PowerShell, instala las dependencias, define la conexión y ejecuta la aplicación:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
$env:DB_HOST = "localhost"
$env:DB_NAME = "simulador_pmp"
$env:DB_USER = "postgres"
$env:DB_PASSWORD = "TU_CONTRASEÑA"
$env:DB_PORT = "5432"
$env:SIMULADOR_SECRET_KEY = "define-una-clave-segura"
python .\conexion.py
python app.py
```

La conexión se configura en el archivo `conexion.py`, en `database_config_from_env()` y `connect_database()`. Estos campos reflejan directamente el ejemplo de conexión: `host`, `database`, `user`, `password` y `port`. Cambia las variables de entorno para apuntar a otro servidor. La contraseña no se almacena en el código. `python .\conexion.py` ejecuta manualmente `check_database_connection()` y verifica la conexión y una consulta al servidor/base; devuelve un mensaje de error y código de salida distinto de cero si falla.

Al iniciar, la aplicación se conecta a la base nueva y crea las tablas definidas en `schema_postgres.sql`. También puedes inicializarlas explícitamente con `python -m flask --app app init-db`. La aplicación necesita permisos para crear tablas, pero no crea la base de datos en el servidor.

## Cuenta Owner y administración

Después de configurar PostgreSQL y crear el esquema, crea la cuenta inicial Owner desde PowerShell. El asistente solicita nombre de usuario, primer nombre, primer apellido, teléfono principal internacional (`+` y código de país), correo y contraseña. Los segundos nombres/apellidos, documento de ID y teléfono secundario son opcionales:

```powershell
python -m flask --app app create-owner
```

La contraseña se oculta mientras se escribe. No hay credenciales Owner predeterminadas. El Owner y los Administradores acceden al panel desde **Administración**. Ambos pueden consultar y activar/desactivar usuarios, crear/editar preguntas manuales y automáticas y aprobar/rechazar las automáticas. Solo el Owner puede crear, desactivar o eliminar Administradores.

Las pruebas de integración usan PostgreSQL. Define `TEST_DATABASE_URL` para una base de pruebas dedicada; cada caso crea y elimina su propio esquema dentro de esa base:

```powershell
$env:TEST_DATABASE_URL = "postgresql://USUARIO:CLAVE@localhost:5432/simulador_pmp_test"
python -m unittest discover -s tests -v
```

## Esquema y datos nuevos

El esquema se define exclusivamente en `schema_postgres.sql`. Al iniciar la aplicación se crean las tablas y los catálogos de clasificación; no se agregan preguntas de demostración ni se importan datos anteriores. El banco de preguntas comienza vacío y se llenará con las nuevas preguntas.

El esquema incluye:

- Cuentas de usuario y superusuario identificadas con UUID de PostgreSQL. Primer nombre, primer apellido y teléfono principal son obligatorios; segundos nombres/apellidos, documento de ID y teléfono secundario son opcionales. Los teléfonos se guardan con código de país.
- Perfil e historial de preguntas de usuarios.
- Superusuarios o administradores.
- Banco de preguntas manuales.
- Banco de preguntas automáticas con validación.
- Reactivos tipados: Case/Scenario Set, Visual-Based, Multiple Choice, Multiple Responses, Drag and Drop, Hotspot y Pull-Down List.

## Funcionalidad del prototipo

- Registro e inicio/cierre de sesión; las contraseñas se almacenan como hashes.
- El acceso desde dispositivos móviles está prohibido; la aplicación bloquea las solicitudes con agentes de usuario móviles.
- Selección de dominios de desempeño, enfoque metodológico y dominio oficial ECO, sujeta al catálogo de combinaciones permitidas.
- Bancos general y personal: las preguntas seleccionadas por una prueba se añaden al banco del usuario; se pueden consultar las usadas, pendientes y favoritas. Se admite el tipo de fuente `automated` para una futura integración.
- Pruebas de 30, 60, 75 o 180 preguntas, únicamente cuando existen suficientes preguntas compatibles para el tamaño elegido. Cada pregunta aporta 76 segundos al temporizador global.
- La administración permite crear y editar los siete tipos de reactivo, buscar preguntas y filtrar los bancos por tipo y estado. El formulario conserva los datos al corregir errores, guía la cantidad de subpreguntas de un caso y valida los campos requeridos según el tipo. Los casos incluyen de 3 a 5 subpreguntas; los tipos visuales aceptan imágenes PNG, JPG o WebP de hasta 5 MB, guardadas bajo `static/uploads/questions`.
- La prueba presenta progreso, temporizador y una navegación filtrable por formato y estado. Muestra los casos en una vista dividida con navegación por subpregunta, permite ampliar los gráficos Visual-Based, limitar y validar las selecciones múltiples, emparejar fichas, seleccionar puntos Hotspot y completar párrafos con desplegables en línea. Las opciones Multiple Choice admiten formato enriquecido seguro con `**negritas**`, `*cursiva*` y `` `código` ``.
- Preguntas no contestadas o guardadas quedan pendientes; al terminar se pueden revisar correctas, incorrectas, ambas o pendientes.

## Alcance

La aplicación no incluye preguntas de muestra. Hasta que se agreguen preguntas al banco, no habrá contenido disponible para iniciar una prueba. La administración incluye paneles Owner/Administrador; no incluye generación real con IA.

## Investigación de clasificación

Se verificaron las fuentes oficiales disponibles al 6 de octubre de 2026:

- El [ECO PMP 2026 (PDF)](https://www.pmi.org/-/media/pmi/documents/public/pdf/certifications/pmp/pmp-examination-content-outline.pdf?rev=b8e1618215b74dfc926f5b406567f072) identifica la actualización del examen de julio de 2026 (p. 2) y señala que fue revisado el 11 de agosto de 2026 (pie de página, p. 4). Sus dominios y ponderaciones son Personas (33 %), Procesos (41 %) y Entorno Empresarial (26 %) (p. 5).
- El mismo ECO indica que los enfoques predictivo, adaptativo/ágil e híbrido aparecen en los tres dominios y no están aislados de ninguno (p. 5). Por tanto, se permiten las nueve combinaciones entre enfoques y dominios ECO.
- El ECO estima aproximadamente 40 % de preguntas predictivas y reparte el 60 % restante entre adaptativas/ágiles e híbridas; también advierte que los conteos exactos varían por forma de examen (p. 5). No se aplican cuotas por enfoque ni por dominio ECO en cada prueba.
- PMI identifica [PMBOK® Guide, Octava Edición](https://www.pmi.org/standards/pmbok) como publicada en noviembre de 2025. Su [tabla de contenidos oficial (PDF)](https://www.pmi.org/-/media/pmi/documents/public/pdf/publications/pmbok-guide-eighth-edition_table-of-contents.pdf) enumera Gobernanza, Alcance, Cronograma, Finanzas, Interesados, Recursos y Riesgo como dominios de desempeño (pp. 7–8).

PMI no publica en esas fuentes una matriz de correspondencia uno-a-uno entre los siete dominios de desempeño PMBOK y los tres dominios ECO, ni combinaciones excluidas entre ellos. La aplicación conserva estos catálogos como dimensiones de clasificación independientes y solo aplica la regla explícita del ECO: los enfoques metodológicos no están aislados de ningún dominio ECO.

La aplicación inicializa en `valid_combinations` las nueve parejas permitidas, valida los filtros del simulador y protege las etiquetas de preguntas frente a combinaciones metodológicas/ECO no registradas.
