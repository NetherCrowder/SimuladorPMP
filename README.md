# Simulador PMP

Prototipo de práctica de exámenes con Python, Flask y PostgreSQL. La aplicación requiere una conexión a PostgreSQL; no crea ni usa una base de datos local.

## Despliegue en internet

Esta aplicación necesita ejecutar Python/Flask y conectarse a PostgreSQL. **GitHub Pages, Netlify y otros alojamientos estáticos no pueden ejecutar el simulador**; si GitHub Pages muestra este README, está publicando la documentación del repositorio, no la aplicación Flask.

El archivo `render.yaml` configura el servicio web gratuito en Render. Para desplegarlo:

1. Crea una base PostgreSQL gratuita en un proveedor compatible, como Neon, y copia su URL de conexión. Usa la URL que requiere SSL (normalmente contiene `sslmode=require`).
2. En Render, crea un **Blueprint** desde este repositorio y configura `DATABASE_URL` con la URL copiada. El Blueprint genera `SIMULADOR_SECRET_KEY` automáticamente.
3. Al terminar el primer despliegue, crea la cuenta Owner desde la consola de Render con `python -m flask --app app create-owner`.
4. Abre la URL del servicio Render para usar el simulador. Al iniciar, la aplicación crea las tablas en la base configurada.

El servicio gratuito de Render puede entrar en reposo y tardar en responder al primer acceso. En Render y Vercel, el sistema de archivos de la aplicación no es persistente: las imágenes cargadas en `static/uploads/questions` pueden perderse al reiniciar o volver a desplegar. Para conservarlas, usa almacenamiento externo de objetos. Los planes y límites gratuitos dependen de cada proveedor.

Si despliegas en Vercel, configura `DATABASE_URL` (o `POSTGRES_URL`, con el valor de conexión de Neon) y `SIMULADOR_SECRET_KEY` en **Project Settings → Environment Variables** para el entorno **Production**; después, vuelve a desplegar. Vercel reconoce la instancia Flask `app` exportada por `app.py`; la base se inicializa en la primera solicitud. Para crear la cuenta Owner desde tu equipo, sigue la sección **Crear Owner de forma segura desde el entorno local**.

`DATABASE_URL` es opcional; si no está definida, la aplicación conserva la configuración local mediante `DB_HOST`, `DB_NAME`, `DB_USER`, `DB_PASSWORD` y `DB_PORT`.

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

### Alternativa: crear el Owner con SQL

Ejecuta este método en la misma base PostgreSQL, después de inicializar el esquema. Primero genera un hash compatible con Werkzeug usando Python; el comando solicita la contraseña sin mostrarla ni guardarla en el historial:

```powershell
python -c "from getpass import getpass; from werkzeug.security import generate_password_hash; print(generate_password_hash(getpass('Contraseña del Owner: ')))"
```

Copia el hash resultante y reemplaza los valores de ejemplo del siguiente SQL. No pegues la contraseña en texto plano en la base de datos. El índice de la base permite un solo Owner; si ya existe uno, la inserción fallará y no debes volver a ejecutar el bloque sin revisar las cuentas existentes.

```sql
BEGIN;

WITH new_owner AS (
    INSERT INTO users (
        username, email, password_hash, created_at, account_role,
        first_name, second_name, first_surname, second_surname,
        document_id, primary_phone, secondary_phone
    )
    VALUES (
        'owner_usuario',
        'owner@ejemplo.com',
        'PEGA_AQUI_EL_HASH_GENERADO',
        EXTRACT(EPOCH FROM clock_timestamp()),
        'owner',
        'Nombre',
        NULL,
        'Apellido',
        NULL,
        NULL,
        '+573001234567',
        NULL
    )
    RETURNING id, email, created_at
),
new_profile AS (
    INSERT INTO user_profiles (user_id, registration_date, email)
    SELECT id, created_at, email
    FROM new_owner
    RETURNING user_id
)
INSERT INTO super_users (user_id, role, status)
SELECT user_id, 'Owner', 'active'
FROM new_profile
RETURNING user_id;

COMMIT;
```

Usa un nombre de usuario y correo únicos, un teléfono internacional válido y un hash generado para la contraseña elegida. Conserva el `user_id` devuelto si necesitas verificar la cuenta.

### Crear Owner de forma segura desde el entorno local

El script `scripts/create_owner_secure.py` conecta tu entorno local directamente a Neon y reutiliza el comando de creación del Owner de la aplicación. Desde la raíz del repositorio:

```powershell
python .\scripts\create_owner_secure.py
```

Si existe `DATABASE_URL`, `POSTGRES_URL` o `POSTGRES_URL_NON_POOLING` en el entorno local, el script la utiliza en ese orden. Si no hay ninguna definida, solicita la URL de Neon con entrada oculta. La URL debe incluir `sslmode=require` (o un modo de verificación TLS más estricto). El script muestra solo el host y el nombre de la base, pide confirmación explícita, comprueba la conexión y entonces inicia el asistente de Owner. La contraseña del Owner también se solicita de forma oculta y se almacena como hash.

No escribas la URL ni la contraseña en el código, en argumentos de comandos o en archivos versionados. En Neon, utiliza una credencial con permisos para inicializar el esquema y crear cuentas. Si falla la conexión, revisa que la URL corresponda al proyecto/base correctos y que el equipo tenga acceso de red.

## Cargar preguntas mediante SQL

El esquema y los catálogos deben estar inicializados antes de insertar preguntas. El ejemplo siguiente agrega **una pregunta manual de opción múltiple**; reemplaza todos los textos de ejemplo. `correct_option` usa índice desde cero (`0` para A, `1` para B, etc.); `correct_answer_json` guarda ese índice como JSON y `correct_answer` en el banco guarda la letra. Cambia las tres etiquetas de clasificación por IDs existentes: dominios de desempeño (`governance`, `scope`, `schedule`, `finance`, `stakeholders`, `resources`, `risk`), metodología (`predictive`, `agile_hybrid`) y dominio ECO (`people`, `processes`, `business_environment`).

```sql
BEGIN;

WITH new_question AS (
    INSERT INTO questions (
        prompt, options_json, option_feedback_json, correct_option,
        question_type, content_json, correct_answer_json, explanation,
        source_type, active, created_at
    )
    VALUES (
        'Escribe aquí el enunciado de la pregunta.',
        '["Opción A","Opción B","Opción C","Opción D"]',
        '["Explicación","Explicación","Explicación","Explicación"]',
        2,
        'multiple_choice',
        '{}',
        '2',
        'Explica por qué la opción C es la mejor respuesta.',
        'general',
        1,
        EXTRACT(EPOCH FROM clock_timestamp())
    )
    RETURNING id
),
performance_tag AS (
    INSERT INTO question_performance_domains (question_id, domain_id)
    SELECT id, 'risk' FROM new_question
    RETURNING question_id
),
methodology_tag AS (
    INSERT INTO question_methodologies (question_id, methodology_id)
    SELECT id, 'predictive' FROM new_question
    RETURNING question_id
),
eco_tag AS (
    INSERT INTO question_eco_domains (question_id, eco_domain_id)
    SELECT id, 'people' FROM new_question
    RETURNING question_id
),
manual_bank_entry AS (
    INSERT INTO manual_question_bank (
        question_id, stem, options, correct_answer, category_type,
        category_value, interaction_config, feedback, added_at, status
    )
    SELECT
        id,
        'Escribe aquí el enunciado de la pregunta.',
        '{"A":"Opción A","B":"Opción B","C":"Opción C","D":"Opción D"}'::jsonb,
        'C',
        'performance_domain',
        'risk',
        '{"max_Selections":1,"options":{"A":"Opción A","B":"Opción B","C":"Opción C","D":"Opción D"},"correct_answers":["C"]}'::jsonb,
        'Explica por qué la opción C es la mejor respuesta.',
        EXTRACT(EPOCH FROM clock_timestamp()),
        TRUE
    FROM new_question
    RETURNING question_id
)
SELECT question_id FROM manual_bank_entry;

COMMIT;
```

Mantén sincronizados el enunciado, las opciones, la respuesta correcta y la explicación en ambas partes del bloque. En `category_value` debe ir el mismo ID que el dominio de desempeño insertado arriba. Los otros seis formatos (`case_set`, `visual_based`, `multiple_responses`, `drag_drop`, `hotspot` y `pull_down`) requieren estructuras JSON específicas en `content_json`, `correct_answer_json` e `interaction_config`; para ellos se recomienda usar el formulario de administración, que valida esos formatos y guarda los datos relacionados.

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
- Banco manual con una etiqueta obligatoria por cada eje (dominio PMBOK v8, dominio ECO 2026 y enfoque de desarrollo). Conserva `feedback` y `interaction_config`; el banco automático también almacena ambos campos.
- Banco automático con validación administrativa. La generación automática no forma parte de esta entrega.
- Reactivos tipados: Case/Scenario Set, Visual-Based, Multiple Choice, Multiple Responses, Drag and Drop, Hotspot y Pull-Down List.

## Funcionalidad del prototipo

- Registro e inicio/cierre de sesión; las contraseñas se almacenan como hashes.
- El acceso desde dispositivos móviles está prohibido; la aplicación bloquea las solicitudes con agentes de usuario móviles.
- El desplegable de tipo/tamaño permite elegir una práctica corta (15 o 30) o un simulacro oficial (60 o 180). En prácticas se elige una ruta en un segundo desplegable y después se muestran sus categorías; el simulacro oficial oculta filtros y presenta el botón para iniciar. Las prácticas cortas permiten elegir dominio PMBOK v8 (multiselección), dominio ECO (selección única) o enfoque de desarrollo (selección única). Los simulacros aplican las cuotas ECO y de enfoque configuradas.
- Bancos general y personal: las preguntas seleccionadas por una prueba se añaden al banco del usuario; se pueden consultar las usadas, pendientes y favoritas. Se admite el tipo de fuente `automated` para una futura integración.
- Prácticas de 15 o 30 preguntas y simulacros oficiales de 60 o 180 preguntas, a 80 segundos por reactivo. Los simulacros distribuyen las preguntas entre Personas (33 %), Procesos (41 %) y Entorno de Negocio (26 %), con 60 % Ágil/Híbrido y 40 % Predictivo.
- Las preguntas heredadas que no tengan un tag en cada eje no se seleccionan para exámenes; un administrador debe completar sus tags antes de volver a incluirlas.
- La administración permite crear y editar los siete tipos de reactivo, buscar preguntas y filtrar los bancos por tipo y estado. El formulario exige una etiqueta de cada eje mediante opciones de selección única, además de conservar los datos al corregir errores, guiar la cantidad de subpreguntas de un caso y validar campos según el tipo. Los casos incluyen de 3 a 5 subpreguntas; los tipos visuales aceptan imágenes PNG, JPG o WebP de hasta 5 MB, guardadas bajo `static/uploads/questions`.
- La configuración de interacción se persiste como JSON por formato: casos con `case_text` y `sub_questions`; reactivos visuales con `media_url`, `alt_text`, `interaction_mode`, `target_area` y `options`; selección única/múltiple con `max_Selections`, `options` y `correct_answers`; arrastrar y soltar con `source_items` y `target_boxes`; y completar espacios con `template_text` y `dropdowns`. Las coordenadas Hotspot se guardan en píxeles en el JSON y se mantienen normalizadas internamente para que la evaluación funcione al redimensionar la imagen.
- En Multiple Responses, `max_Selections` se deriva del número de respuestas correctas para que coincida con la validación del examen. Si se usan tres respuestas correctas, el valor debe ser `3` (aunque el ejemplo de formato indique `2`).
- La prueba presenta progreso, temporizador y una navegación filtrable por formato y estado. Muestra los casos en una vista dividida con navegación por subpregunta, permite ampliar los gráficos Visual-Based, limitar y validar las selecciones múltiples, emparejar fichas, seleccionar puntos Hotspot y completar párrafos con desplegables en línea. Las opciones Multiple Choice admiten formato enriquecido seguro con `**negritas**`, `*cursiva*` y `` `código` ``.
- Preguntas no contestadas o guardadas quedan pendientes; al terminar se pueden revisar correctas, incorrectas, ambas o pendientes.
- El panel del usuario muestra la consistencia por categoría como el promedio de sus tres intentos más recientes en cada ruta. Los resultados de simulacros oficiales incluyen score global y desglose ECO/metodología frente a la línea base del 70 %.

## Alcance

La aplicación no incluye preguntas de muestra. Hasta que se agreguen preguntas al banco, no habrá contenido disponible para iniciar una prueba. La administración incluye paneles Owner/Administrador; no incluye generación real con IA.

## Investigación de clasificación

Se verificaron las fuentes oficiales disponibles al 6 de octubre de 2026:

- El [ECO PMP 2026 (PDF)](https://www.pmi.org/-/media/pmi/documents/public/pdf/certifications/pmp/pmp-examination-content-outline.pdf?rev=b8e1618215b74dfc926f5b406567f072) identifica la actualización del examen de julio de 2026 (p. 2) y señala que fue revisado el 11 de agosto de 2026 (pie de página, p. 4). Sus dominios y ponderaciones son Personas (33 %), Procesos (41 %) y Entorno Empresarial (26 %) (p. 5).
- El mismo ECO indica que los enfoques predictivo, adaptativo/ágil e híbrido aparecen en los tres dominios y no están aislados de ninguno (p. 5). La aplicación agrupa adaptativo/ágil e híbrido como el tag `agile_hybrid`.
- El ECO estima aproximadamente 40 % de preguntas predictivas y reparte el 60 % restante entre adaptativas/ágiles e híbridas; también advierte que los conteos exactos varían por forma de examen (p. 5). Los simulacros del producto aplican la distribución de referencia configurada (60 % Ágil/Híbrido y 40 % Predictivo), cruzada con los porcentajes ECO.
- PMI identifica [PMBOK® Guide, Octava Edición](https://www.pmi.org/standards/pmbok) como publicada en noviembre de 2025. Su [tabla de contenidos oficial (PDF)](https://www.pmi.org/-/media/pmi/documents/public/pdf/publications/pmbok-guide-eighth-edition_table-of-contents.pdf) enumera Gobernanza, Alcance, Cronograma, Finanzas, Interesados, Recursos y Riesgo como dominios de desempeño (pp. 7–8).

PMI no publica en esas fuentes una matriz de correspondencia uno-a-uno entre los siete dominios de desempeño PMBOK y los tres dominios ECO. El simulador registra los tres tags de forma independiente; en prácticas, el usuario elige una ruta de filtrado y en simulacros oficiales se aplican las cuotas ECO y de enfoque definidas para el producto.

La IA no se integra en esta entrega: la generación se desarrolla en un notebook de laboratorio y las recomendaciones de desempeño quedan para una fase posterior.
