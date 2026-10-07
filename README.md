# Simulador PMP

Prototipo local de práctica de exámenes con Python, Flask y SQLite. Esta primera etapa se centra en la plataforma; no integra generación de preguntas con IA.

## Ejecutar localmente

En PowerShell, desde la carpeta del proyecto:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python app.py
```

Abre `http://127.0.0.1:5000`. La base SQLite y una clave local para sesiones se crean automáticamente dentro de `instance\`. Para cambiar la clave de sesión, define `SIMULADOR_SECRET_KEY` antes de iniciar Flask. No uses la clave local generada automáticamente como estrategia de configuración para un despliegue público.

La base de datos también puede inicializarse mediante `python -m flask --app app init-db`.

## Funcionalidad del prototipo

- Registro e inicio/cierre de sesión; las contraseñas se almacenan como hashes.
- Aviso y bloqueo de experiencia para agentes de usuario detectados como móviles. Esta detección es orientativa y puede falsificarse; no es un control de seguridad.
- Selección de dominios de desempeño, enfoque metodológico y dominio oficial ECO, sujeta al catálogo de combinaciones permitidas.
- Bancos general y personal: las preguntas seleccionadas por una prueba se añaden al banco del usuario; se pueden consultar las usadas, pendientes y favoritas. Se admite el tipo de fuente `automated` para una futura integración.
- Pruebas de 30, 60, 75 o 180 preguntas, únicamente cuando existen suficientes preguntas compatibles para el tamaño elegido. Cada pregunta aporta 76 segundos al temporizador global.
- Preguntas no contestadas o marcadas como guardadas quedan pendientes. Al terminar, se pueden revisar las correctas, incorrectas, ambas o pendientes. Cada opción de respuesta tiene su propia retroalimentación sobre por qué es correcta o incorrecta; las preguntas pendientes no revelan la respuesta. No se muestran tips.

## Datos de demostración y alcance

El contenido de ejemplo es ficticio y sirve para probar la interfaz, los filtros y el flujo de examen; no representa preguntas oficiales del PMP ni material de estudio válido. El banco inicial no necesariamente contiene suficientes preguntas para iniciar las pruebas más largas. No incluye panel administrativo ni generación real con IA; esos contenidos se podrán reemplazar o integrar posteriormente.

## Investigación de clasificación

Se verificaron las fuentes oficiales disponibles al 6 de octubre de 2026:

- El [ECO PMP 2026 (PDF)](https://www.pmi.org/-/media/pmi/documents/public/pdf/certifications/pmp/pmp-examination-content-outline.pdf?rev=b8e1618215b74dfc926f5b406567f072) identifica la actualización del examen de julio de 2026 (p. 2) y señala que fue revisado el 11 de agosto de 2026 (pie de página, p. 4). Sus dominios y ponderaciones son Personas (33 %), Procesos (41 %) y Entorno Empresarial (26 %) (p. 5).
- El mismo ECO declara: “Predictive, adaptive/agile, and hybrid management approaches will be found throughout the three domain areas listed above and are not isolated to any particular domain or task” (p. 5). Por lo tanto, **las nueve combinaciones** entre Predictiva/Ágil/Híbrida y Personas/Procesos/Entorno Empresarial están permitidas; no se restringe ninguna de ellas.
- El ECO estima aproximadamente 40 % de preguntas predictivas y reparte el 60 % restante entre adaptativas/ágiles e híbridas; también advierte que los conteos exactos varían por forma de examen (p. 5). No se aplican cuotas por enfoque ni por dominio ECO en cada prueba.
- PMI identifica [PMBOK® Guide, Octava Edición](https://www.pmi.org/standards/pmbok) como publicada en noviembre de 2025. Su [tabla de contenidos oficial (PDF)](https://www.pmi.org/-/media/pmi/documents/public/pdf/publications/pmbok-guide-eighth-edition_table-of-contents.pdf) enumera Gobernanza, Alcance, Cronograma, Finanzas, Interesados, Recursos y Riesgo como dominios de desempeño (pp. 7–8).

PMI no publica en esas fuentes una matriz de correspondencia uno-a-uno entre los siete dominios de desempeño PMBOK y los tres dominios ECO, ni combinaciones excluidas entre ellos. La aplicación conserva estos catálogos como dimensiones de clasificación independientes y solo aplica la regla explícita del ECO: los enfoques metodológicos no están aislados de ningún dominio ECO.

La aplicación inicializa en `valid_combinations` las nueve parejas permitidas, valida los filtros del simulador y protege las etiquetas de preguntas frente a combinaciones metodológicas/ECO no registradas. Las nueve preguntas ficticias iniciales están distribuidas por estas parejas para probar los filtros; sus etiquetas no constituyen una clasificación educativa oficial.

## Pruebas

```powershell
python -m unittest discover -s tests -v
```
