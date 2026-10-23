# Especificación Técnica - Simulador PMP (ECO 2026 / PMBOK v8)

Este documento detalla la arquitectura de datos, las estrictas reglas de navegación y filtrado (UI/UX), y las reglas de métricas para el desarrollo del backend y frontend del simulador PMP.

## 1. Modelo de Datos del Banco de Preguntas (Matriz Tridimensional)

Cada pregunta en la base de datos debe tener **3 etiquetas (tags) obligatorias** para permitir los cruces de filtros y la generación de métricas.

### Estructura del JSON (Ejemplo de Objeto)
```json
{
  "id_pregunta": "PMP-001",
  "contexto": "El gerente de proyecto tiene un proyecto que está en estos momentos trabado con una decisión crítica sobre el diseño, y el equipo no encuentra cómo lograr tomar la decisión final, generando retrasos.",
  "opciones": {
    "A": "Escalar el problema inmediatamente al patrocinador para que tome la decisión.",
    "B": "Realizar una votación por mayoría simple con el equipo técnico.",
    "C": "Reunirse con el cliente y el patrocinador para definir un marco de toma de decisiones a futuro y documentarlo como lección aprendida.",
    "D": "Detener el proyecto hasta que el comité de control de cambios lo resuelva."
  },
  "respuesta_correcta": "C",
  "feedback": "La decisión debe acordarse con las partes interesadas pertinentes y documentarse para orientar decisiones futuras.",
  "option_feedback": [
    "Escalar inmediatamente evita que el equipo establezca un marco de decisión.",
    "Una votación simple no garantiza que participen las partes interesadas adecuadas.",
    "La respuesta correcta establece y documenta un marco de toma de decisiones.",
    "Detener el proyecto no resuelve cómo se tomarán decisiones similares en el futuro."
  ],
  "tags": {
    "dominio_pmbok_v8": "Gobernanza", 
    "dominio_eco_2026": "Entorno de Negocio", 
    "enfoque_desarrollo": "Predictivo" 
  }
}
```

El **feedback o sustento general** es obligatorio: explica por qué la respuesta correcta es adecuada y debe mostrarse al usuario al revisar sus resultados. El `option_feedback` es opcional; cuando se incluya, debe tener una explicación por opción y mantener el mismo orden que `opciones`. Este contenido es formativo: no altera la selección de preguntas ni el cálculo de la puntuación.

En la base de datos actual, `questions.explanation` almacena el `feedback` general y `questions.option_feedback_json` almacena el `option_feedback`. No se debe crear una segunda columna de sustento general que duplique `explanation`.

En PostgreSQL, los tags se guardan en las tablas de relación `question_performance_domains`, `question_eco_domains` y `question_methodologies`, con exactamente una relación por eje y pregunta. Las tablas `manual_question_bank` y `automated_question_bank` conservan el `feedback` y `interaction_config` propios del banco y formato del reactivo.

### Catálogo Estricto de Etiquetas (Base de Datos)
* **dominio_pmbok_v8** (1 de 7): `Gobernanza`, `Alcance`, `Cronograma`, `Finanzas`, `Interesados`, `Recursos`, `Riesgos`.
* **dominio_eco_2026** (1 de 3): `Procesos`, `Personas`, `Entorno de Negocio`.
* **enfoque_desarrollo** (1 de 2): `Predictivo`, `Ágil/Híbrido`.

---

## 2. Reglas de Interfaz (UI) y Filtros para Prácticas

El menú de configuración ofrece un desplegable de tipo/tamaño con las opciones **Práctica corta (15 o 30 preguntas)** y **Simulacro oficial (60 o 180 preguntas)**. En práctica corta se muestra un segundo desplegable para elegir una ruta y, al seleccionarla, sus categorías. En simulacro oficial se ocultan las rutas y categorías y solo se muestra el botón **Iniciar prueba** junto con el selector de tipo/tamaño. El temporizador global calcula **80 segundos (1 minuto y 20 seg)** por pregunta en cualquier modo.

### BLOQUE A: Prácticas Cortas (Filtros Manuales)
Para iniciar una prueba de 15 o 30 preguntas, la interfaz debe obligar al usuario a elegir en un desplegable **UNA SOLA DE TRES RUTAS MUTUAMENTE EXCLUYENTES** y después mostrar solo las opciones de esa ruta.
*⚠️ REGLA CRÍTICA PARA EL DEV:* No se pueden cruzar rutas. El usuario no puede seleccionar "Procesos" (Ruta 2) y al mismo tiempo "Predictivo" (Ruta 3). Se elige la Ruta, y luego las opciones dentro de ella.

* **RUTA 1: Por Dominios de Desempeño (PMBOK v8)**
  * **Comportamiento:** ÚNICA ruta que permite **Multiselección** (Checkbox).
  * **Regla:** El usuario puede seleccionar desde 1 hasta los 7 dominios simultáneamente (Ej. `Gobernanza` + `Riesgos` + `Alcance`).
  * **Consulta (Pseudo-SQL):** `SELECT * FROM preguntas WHERE dominio_pmbok_v8 IN ([array_selecciones]) LIMIT 15|30`

* **RUTA 2: Por Dominios ECO 2026**
  * **Comportamiento:** Selección Única y Excluyente (Radio Button).
  * **Regla:** El usuario selecciona SOLO UNA opción: `Procesos` OR `Personas` OR `Entorno de Negocio`. Ignora las otras variables.
  * **Consulta:** `SELECT * FROM preguntas WHERE dominio_eco_2026 = 'Procesos' LIMIT 15|30`

* **RUTA 3: Por Enfoques de Desarrollo**
  * **Comportamiento:** Selección Única y Excluyente (Radio Button).
  * **Regla:** El usuario selecciona SOLO UNA opción: `Predictivo` OR `Ágil/Híbrido`.
  * **Consulta:** `SELECT * FROM preguntas WHERE enfoque_desarrollo = 'Ágil/Híbrido' LIMIT 15|30`

---

### BLOQUE B: Simulacros Reales (60 y 180 Preguntas)
En estos modos **la interfaz desactiva los filtros manuales**. El algoritmo trae **60 preguntas (Mini - 80 mins)** o **180 preguntas (Full - 240 mins)** forzando estrictamente esta distribución desde el backend:
* **Eje ECO 2026 (100%):** Personas (33%), Procesos (41%), Entorno de Negocio (26%).
* **Eje Enfoques (100%):** Ágil/Híbrido (60%), Predictivo (40%).
* El backend selecciona aleatoriamente por cada cruce ECO/enfoque para conservar ambas distribuciones. Cuotas enteras por tamaño:

| Tamaño | Personas Ágil/Híbrido | Personas Predictivo | Procesos Ágil/Híbrido | Procesos Predictivo | Entorno Ágil/Híbrido | Entorno Predictivo |
|---:|---:|---:|---:|---:|---:|---:|
| 60 | 12 | 8 | 15 | 10 | 9 | 6 |
| 180 | 36 | 23 | 44 | 30 | 28 | 19 |

En 180 preguntas, los totales ECO resultan 59/74/47 (redondeo a preguntas enteras) y los totales por enfoque 108/72.

---

## 3. Lógica de Evaluación y Dashboards de Resultados

### 3.1 La Técnica de la Consistencia (Prácticas Cortas)
Para medir el progreso en un tema específico (Ej. Prácticas de la RUTA 1), el dashboard no debe mostrar un promedio histórico total. 
* **Regla de Negocio:** Calcular el **Promedio de los últimos 3 intentos** en esa categoría específica.
* **Lógica (Pseudo-SQL):** `SELECT AVG(score) FROM (SELECT score FROM examenes WHERE usuario_id = X AND categoria_evaluada = 'Gobernanza' ORDER BY fecha DESC LIMIT 3)`
* **UI/UX:** Mostrar un indicador que diga: *"Tu Consistencia actual: X% (Promedio de tus últimas 3 pruebas)"*.

### 3.2 Desglose de Resultados (Simulacros de 60 y 180 preguntas)
Al finalizar un simulacro, el frontend debe graficar una "radiografía" detallada del rendimiento, comparada siempre contra una **Línea Base (Target) del 70%**.

1. **Score Global:** % total de respuestas correctas (Ej. 73%).
2. **Gráfico 1: Desglose por Dominios ECO 2026:**
   * % de acierto evaluando SOLO las preguntas de *Procesos* que salieron en ese examen.
   * % de acierto evaluando SOLO las de *Personas*.
   * % de acierto evaluando SOLO las de *Entorno de Negocio*.
3. **Gráfico 2: Desglose por Enfoques de Desarrollo:**
   * % de acierto evaluando SOLO las preguntas *Predictivas*.
   * % de acierto evaluando SOLO las *Ágiles/Híbridas*.

---

## 4. Ejemplos de Preguntas (Validación de Tags)

**Ejemplo 1: (Tag Principal: Ágil / Procesos / Cronograma)**
* **Contexto:** Se está utilizando un diagrama de red y el método de ruta crítica para calcular las reservas de tiempo de una iteración híbrida que combina software y hardware.
* **Opciones:** A) Reducir la iteración. B) Ajustar la ruta crítica... *(Correcta: B)*
* **Tags:** `dominio_pmbok_v8`: Cronograma | `dominio_eco_2026`: Procesos | `enfoque_desarrollo`: Ágil/Híbrido.

**Ejemplo 2: (Tag Principal: Ágil / Personas / Interesados)**
* **Contexto:** Un equipo Scrum tiene problemas continuos de comunicación con el Product Owner. El Scrum Master interviene para facilitar un acuerdo.
* **Opciones:** A) Cambiar al PO. B) Facilitar un taller de alineación... *(Correcta: B)*
* **Tags:** `dominio_pmbok_v8`: Interesados | `dominio_eco_2026`: Personas | `enfoque_desarrollo`: Ágil/Híbrido.