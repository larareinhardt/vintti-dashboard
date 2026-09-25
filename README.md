# Agente de métricas — Vintti

Bot interno de Slack que explica en lenguaje simple las métricas de negocio de
Vintti (Staffing/Recruiting), consultando datos reales en vez de inventar
números. Pensado para managers no técnicos: preguntás algo como *"¿por qué
bajó el NRR este mes?"* y contesta con el dato real y el "por qué".

## Arquitectura, en criollo

```
Slack (DM o @mención)
      │
      ▼
app/slack_bot.py   ← recibe el mensaje, arma la respuesta, la manda de vuelta
      │
      ▼
app/agent.py       ← el "cerebro": le pasa la pregunta a la API de Claude,
      │               junto con la lista de herramientas que puede usar
      │
      ├──> app/metrics.py       (fórmulas propias, ya verificadas contra el dashboard)
      ├──> app/dashboard_api.py (le pide el dato directo a la misma API que usa
      │                          el dashboard visual de Vintti — así siempre coincide)
      ├──> app/db.py            (consulta SQL de solo lectura a la base de Postgres,
      │                          para preguntas puntuales sin fórmula armada)
      └──> app/audit_log.py     (guarda cada pregunta y cada consulta en logs/audit.jsonl)
```

Claude decide solo qué herramienta usar según la pregunta — no hay un menú fijo
de opciones. Si no hay una fórmula ya armada ni una tarjeta en el dashboard
parecida, como último recurso arma una consulta SQL él mismo (avisando que ese
número no está pre-verificado como el resto).

Ver **`definiciones_metricas_vintti.docx`** (en este mismo proyecto) para el
detalle de qué significa cada métrica y de dónde sale exactamente.

## Cómo correrlo

```bash
pip install -r requirements.txt
python -m app.slack_bot
```

Necesita un archivo `.env` (no se sube al repo) con:

```
DB_HOST=...
DB_PORT=...
DB_NAME=...
DB_USER=...           # usuario de SOLO LECTURA de la base de Vintti
DB_PASSWORD=...
ANTHROPIC_API_KEY=...
SLACK_BOT_TOKEN=...
SLACK_APP_TOKEN=...
DASHBOARD_API_BASE=https://7m6mw95m8y.us-east-2.awsapprunner.com
DASHBOARD_API_EMAIL=lara@vintti.com
```

Para probar sin pasar por Slack: `python test_cli.py` (conversación por
terminal).

## Cómo agregar una métrica nueva

Hay dos caminos, según de dónde sale el dato:

1. **Ya existe una tarjeta en el dashboard de Vintti para eso.** No hace falta
   escribir SQL: buscá el `chart_key` en la tabla `dashboard_charts` de la base
   (por título o `dataset_key`), y listo — el agente ya sabe usar
   `get_dashboard_chart` para cualquier `chart_key`. Si no sabés cuál es,
   `search_dashboard_charts` lo busca solo.
2. **No hay tarjeta, o la fórmula la armamos nosotros a mano** (como MRR,
   churn, NRR/GRR). Agregar una función nueva en `app/metrics.py`, registrarla
   en el diccionario `METRICS` (si es una métrica "por mes") o como una
   herramienta aparte en `app/agent.py` (si no depende de un mes puntual).
   **Importante: antes de escribir la fórmula, verificarla contra un número
   real del dashboard** — varias veces esta sesión una fórmula "razonable"
   resultó estar mal (mezclaba Staffing con Recruiting, usaba mal una fecha de
   corte, etc.). No confiar en una fórmula sin comparar contra un número
   conocido primero.

Para lo que no es ninguna de las dos cosas (una pregunta puntual, sin fórmula
ni tarjeta), el agente ya tiene `query_database` para armar un SELECT al
momento — no hace falta agregar nada, pero esos números no están
pre-verificados, y el agente lo aclara en la respuesta.

## Cómo rotar los secretos

Todos los secretos viven en las variables de entorno (`.env` local, o las
variables de entorno del servicio donde esté desplegado — nunca en el código).

- **ANTHROPIC_API_KEY**: se genera en console.anthropic.com → API Keys. Crear
  una nueva, actualizar la variable de entorno, borrar la vieja.
- **SLACK_BOT_TOKEN / SLACK_APP_TOKEN**: se regeneran desde
  api.slack.com/apps → la app "Dashboard Agent" → OAuth & Permissions
  (Bot Token) / Basic Information (App-Level Token).
- **DB_PASSWORD**: se cambia desde la consola de AWS (RDS), coordinando con
  quien administre esa base — es un usuario de solo lectura compartido, avisar
  antes de rotarlo para no cortar el acceso sin avisar.

Después de rotar cualquiera de estos, hay que actualizar la variable de
entorno en el servicio donde esté corriendo el bot y reiniciarlo.

## Estado del proyecto / lo que falta

Este agente arrancó cubriendo la pestaña "Growth" (Management Dashboard) del
dashboard de Vintti. Quedan por sumar las métricas de Account Management,
Sales, Operations y Marketing — el plan es ir pestaña por pestaña,
investigando primero (schema + Glosario del dashboard) antes de escribir
código, no adivinando en vivo.
