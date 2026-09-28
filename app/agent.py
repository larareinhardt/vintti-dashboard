from __future__ import annotations

import json
import os
from datetime import date, datetime
from decimal import Decimal

import anthropic
from anthropic import Anthropic
from dotenv import load_dotenv

from .audit_log import log_event
from .dashboard_api import get_dashboard_chart, search_dashboard_charts
from .db import get_schema_info, run_readonly_query
from .metrics import METRICS, get_opportunities_by_sales_lead, get_weekly_comparison

load_dotenv()

client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

MODEL = "claude-sonnet-5"

SYSTEM_PROMPT = """Sos el agente interno de metricas de Vintti (empresa de staffing/recruiting).
Tu trabajo es explicarle a managers no tecnicos las metricas del negocio en criollo,
sin que tengan que mirar tablas ni dashboards ellos mismos.

REGLA PRINCIPAL - DE DONDE SACAR EL DATO (respetala SIEMPRE, esta por encima de todo):
Primero buscá SIEMPRE el dato en el dashboard. Recien si el dashboard no lo tiene, consultá
la base de datos directo. En concreto, en este orden y sin saltear pasos:
  1) El dashboard y las metricas ya verificadas: get_metric, compare_week_over_week,
     get_dashboard_chart, y get_opportunities_by_sales_lead. Estos numeros coinciden con lo
     que ve el equipo en el dashboard, son la fuente de verdad. SIEMPRE empeza por aca.
  2) Si no conoces de memoria la tarjeta del dashboard que hace falta, usa
     search_dashboard_charts para encontrarla y despues get_dashboard_chart. Segui siendo
     dashboard: sigue teniendo prioridad sobre la base.
  3) SOLO como ultimo recurso, cuando de verdad ni las metricas ni ninguna tarjeta del
     dashboard tienen el dato (ya intentaste, incluida una busqueda con search_dashboard_charts
     y no aparecio nada relevante): recien ahi usa get_schema_info + query_database para
     consultar la base directo. Nunca arranques por aca ni la uses para algo que el dashboard
     ya podria responder.
Cuando termines usando la base directo (query_database), decilo en la respuesta con una linea
clara ("esto no estaba en el dashboard, lo saque consultando la base directo, no esta verificado
como el resto") ademas de la cita de fuente de abajo.

Reglas:
- Antes de responder cualquier pregunta sobre numeros, USA la herramienta get_metric
  para traer los datos reales. Nunca inventes ni estimes numeros de memoria.
- Si la pregunta menciona un mes, usa formato 'YYYY-MM'. Si no menciona mes, asumi el mes
  actual segun la fecha de hoy que se te da en el contexto.
- Respondé corto y directo, en espanol, tono profesional pero informal (como hablando
  con un colega). Nada de jerga tecnica de bases de datos ni nombres de tablas/columnas.
- Si el numero bajo o subio, explica el "por que" usando el detalle (nombres de clientes
  o candidatos que se fueron, etapas del pipeline, etc), no solo el numero.
- Si no tenes suficiente informacion para responder algo (ej: piden una metrica que no
  esta soportada todavia), decilo con claridad y no inventes.
- Citá de donde salio el dato, breve, al final de la respuesta (una linea chica, no un
  parrafo): si uso una metrica ya armada, nombrala (ej: "fuente: metrica mrr"); si uso una
  tarjeta del dashboard, nombrala (ej: "fuente: dashboard, tarjeta Active Pipeline"); si uso
  query_database (SQL libre, no verificado), decilo explicitamente como tal (ej: "fuente:
  consulta SQL armada al momento, no verificada como las metricas de arriba") para que quede
  claro que ese numero no paso por el mismo control de calidad que el resto.
- Si el dato exacto esta disponible en el resultado de una herramienta, respondelo directo
  y con seguridad. No adivines ni ofrezcas "lo mas probable es que sea..." mezclando datos
  de otro periodo cuando la herramienta correcta (ej: compare_week_over_week para preguntas
  de la semana pasada) ya te da el nombre exacto.
- Formato para Slack (esto no es markdown normal, es distinto):
  - Negrita: un solo asterisco de cada lado, ej *MRR*. NUNCA uses doble asterisco (**asi**),
    en Slack se ve mal (aparecen los asteriscos sueltos en vez de negrita).
  - Listas: usa guion medio "-" al principio de la linea, no numeros ni viñetas raras.
  - No uses encabezados con "#", Slack no los interpreta. Para separar secciones, usa un
    emoji corto al principio de la linea (📊 para numeros, 📈 sube, 📉 baja, ✅ dato positivo,
    ⚠️ dato para prestar atencion) en vez de titulos en mayuscula.
  - Mantene parrafos cortos con un salto de linea en blanco entre secciones, para que se lea
    comodo en el celular.
- Ojo con no confundir estas tres (parecidas pero distintas, segun el glosario oficial del
  dashboard): ARPA y ARPC usan REVENUE (salario + fee); "Staffing fee avg" usa solo el FEE
  de Vintti (sin el salario). Si preguntan por "Staffing fee avg" (asi se llama esa tarjeta
  en el dashboard), usa la metrica staffing_fee_avg, no arpc.
- MRR, GMRR, ARPA, ARPC, churn y NRR/GRR son metricas del negocio de Staffing (ingreso
  recurrente) unicamente. Recruiting es un negocio de colocaciones puntuales, separado, y
  todavia no tiene metricas propias soportadas aca. Si preguntan por Recruiting con estas
  metricas, aclara que no esta soportado todavia (no inventes un numero mezclando ambos).
- Si preguntan que cambio de una semana a otra (o "semana a semana") de Staffing, usa la
  herramienta compare_week_over_week (ya incluye MRR, clientes/candidatos, Churn M3 y pipeline).
- Si la pregunta no coincide con ninguna herramienta ni chart_key conocido, usa
  search_dashboard_charts. Probá primero con 1-2 palabras clave generales (ej: "leads",
  "win rate", "lifetime") antes de probar variantes mas largas o especificas -- no repitas
  busquedas parecidas mas de 2-3 veces, si no encontras nada relevante decile a Lara que no
  identificaste la tarjeta correcta en vez de seguir probando indefinidamente.
- Si ni las herramientas puntuales ni el dashboard tienen lo que preguntan, usa get_schema_info
  y query_database para consultar la base directo. A diferencia de las otras herramientas (que
  ya estan verificadas), esto lo armas vos en el momento -- si el resultado es para algo
  importante (plata, comisiones, decisiones de negocio), aclaralo explicitamente ("esto no esta
  verificado como las otras metricas, armalo con cuidado y avisame si algo no cierra") y mostra
  que tablas/filtros usaste, para que se pueda revisar. Recorda las reglas de negocio que ya
  aprendimos (Staffing vs Recruiting son negocios distintos, fee != revenue, "activo" incluye
  contratos firmados hasta 30 dias a futuro, etc.) al armar la consulta. Nunca traigas columnas
  sensibles (password, DNI, telefono, direccion, email personal, etc.).
"""

TOOLS = [
    {
        "name": "get_metric",
        "description": (
            "Trae los datos reales de una metrica de negocio de Vintti desde la base de datos. "
            "Metricas disponibles:\n"
            + "\n".join(f"- {name}: {info['description']}" for name, info in METRICS.items())
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "metric": {"type": "string", "enum": list(METRICS.keys())},
                "month": {
                    "type": "string",
                    "description": (
                        "Mes a consultar en formato YYYY-MM. Requerido salvo para 'pipeline', "
                        "'replacement_coverage_30d' y 'clients_multi_candidate' (esas no dependen "
                        "de un mes puntual)."
                    ),
                },
            },
            "required": ["metric"],
        },
    },
    {
        "name": "compare_week_over_week",
        "description": (
            "Compara la semana actual contra la semana anterior completa (Staffing): MRR/GMRR, "
            "clientes y candidatos activos, altas, bajas, reactivados, buyouts, staffing fee avg "
            "y NRR/GRR. Los numeros agregados vienen de la misma tabla que usa el dashboard, asi "
            "que siempre coinciden. Incluye el detalle (nombres) de quien se dio de baja en cada "
            "semana, y de quien entro nuevo (nombre y fee inicial). Incluye Churn M3 y el pipeline "
            "activo (cantidad, revenue ponderado, win rate por modelo) para ambas semanas puntuales."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_dashboard_chart",
        "description": (
            "Trae datos directo de la API interna que usa el propio dashboard de Vintti (la misma "
            "fuente, siempre coincide). Usar esto para preguntas que no cubren las otras "
            "herramientas. Chart_keys ya confirmados y probados:\n"
            "- am_kpi_candidate_churn_window: Churn M3 (dataset candidate_churn_window_summary). "
            "Devuelve candidatos, activos_al_corte, bajas, churn_pct, buyout_pct, etc.\n"
            "- gr_kpi_revenue_ytd: Gross Revenue YTD, Staffing + Recruiting (dataset "
            "revenue_ytd_total). Devuelve revenue_staffing_ytd, revenue_recruiting_ytd, "
            "revenue_total_ytd, y los mismos del anio pasado (_py) con variacion (_yoy_pct).\n"
            "- gr_kpi_position_lifetime: Position Lifetime (dataset position_lifetime_summary). "
            "Devuelve avg_months, avg_months_closed, positions_active, positions_closed, "
            "positions_total, distribucion por buckets de meses, y datos de reemplazos "
            "(positions_with_replacement, replacements_total).\n"
            "- am_line_candidate_churn_window: Historial MENSUAL (no semanal, no existe a nivel "
            "semana) de Churn M3 (dataset candidate_churn_window_history). Cada fila es un mes: "
            "mes, activos_al_cierre, bajas, churn_pct, starts. Usar esto si preguntan como vino "
            "cambiando el Churn M3 mes a mes.\n"
            "- gr_kpi_new_opps_am_windows: New opportunities by AM (dataset "
            "new_opps_am_windows). Necesita params: opp_stage (ej 'Close Win'), window (ej "
            "'30d'), grain (ej 'month'), subtab (ej 'staffing'). Devuelve opps_last_week, "
            "opps_wtd, opps_last_month, opps_mtd, opps_range.\n"
            "Si preguntan algo que no esta en esta lista ni en las otras herramientas, usa primero "
            "la herramienta search_dashboard_charts para encontrar el chart_key correcto (nunca "
            "inventes uno al azar)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "chart_key": {"type": "string"},
                "params": {
                    "type": "object",
                    "description": "Filtros opcionales de la tarjeta (window, grain, subtab, opp_stage, etc.), solo si se conocen.",
                },
            },
            "required": ["chart_key"],
        },
    },
    {
        "name": "search_dashboard_charts",
        "description": (
            "Busca en el catalogo completo de las 289 tarjetas de TODO el dashboard de Vintti "
            "(todas las pestanias: Growth, Account Management, Sales, Operations, Marketing) por "
            "palabras clave en el titulo, para encontrar el chart_key correcto antes de usar "
            "get_dashboard_chart. Usar SIEMPRE que la pregunta no coincida con ninguna herramienta "
            "ni chart_key ya conocido. Ojo: la mayoria de estas tarjetas todavia no fueron "
            "probadas ni verificadas -- si el resultado de get_dashboard_chart despues de "
            "encontrar el chart_key se ve raro o incompleto, decilo con claridad en vez de "
            "inventar una explicacion."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Palabras clave a buscar (ej: 'win rate sales', 'headcount', 'lifetime cliente').",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_opportunities_by_sales_lead",
        "description": (
            "Cantidad de oportunidades de venta por sales lead (quien la lleva: Lara, Mariano, "
            "Bahia, Agustin, Mia), opcionalmente filtrado por etapa. Usar esto para preguntas de "
            "'quien cerro mas' o 'cuantas oportunidades tiene cada uno'. Es todo el historico, no "
            "depende de un mes puntual. No hay tarjeta de dashboard para esto, es una consulta "
            "directa a la base."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "stage": {
                    "type": "string",
                    "description": "Etapa a filtrar (ej 'Close Win' para saber quien cerro). Si no se pasa, trae todas las etapas.",
                },
            },
        },
    },
    {
        "name": "get_schema_info",
        "description": (
            "Lista las tablas de la base de Vintti (sin parametro) o las columnas de una tabla "
            "puntual (con el parametro table). Usar esto ANTES de query_database para saber que "
            "tablas/columnas existen, en vez de adivinar nombres."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "table": {
                    "type": "string",
                    "description": "Nombre de una tabla puntual para ver sus columnas. Si se omite, devuelve la lista de todas las tablas.",
                },
            },
        },
    },
    {
        "name": "query_database",
        "description": (
            "Ejecuta una consulta SQL de SOLO LECTURA (un unico SELECT, sin ';' en el medio) "
            "directo contra la base de Vintti, para preguntas puntuales que no tienen una "
            "herramienta ya armada ni una tarjeta de dashboard. Usar get_schema_info primero si "
            "no se conocen los nombres exactos de tablas/columnas. OJO: a diferencia de las demas "
            "herramientas, esto NO esta pre-verificado -- la formula la arma el modelo en el momento. "
            "Si el resultado es para una decision de negocio importante (plata, comisiones, etc.), "
            "aclaralo explicitamente como un calculo no verificado, mostra la logica que usaste, y "
            "NUNCA selecciones columnas sensibles como password, reset_token, DNI, direccion, "
            "telefono, email personal, etc."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "sql": {
                    "type": "string",
                    "description": "La consulta SELECT a ejecutar.",
                },
            },
            "required": ["sql"],
        },
    },
]


def _json_default(obj):
    if isinstance(obj, (Decimal,)):
        return float(obj)
    if isinstance(obj, (date, datetime)):
        return obj.isoformat()
    return str(obj)


def _run_tool(name: str, tool_input: dict) -> str:
    log_event("herramienta", nombre=name, input=tool_input)

    if name == "compare_week_over_week":
        try:
            return json.dumps(get_weekly_comparison(), default=_json_default)
        except Exception as e:  # noqa: BLE001
            return json.dumps({"error": f"error consultando la base: {e}"})

    if name == "get_dashboard_chart":
        chart_key = tool_input.get("chart_key")
        params = tool_input.get("params") or {}
        try:
            return json.dumps(get_dashboard_chart(chart_key, **params), default=_json_default)
        except Exception as e:  # noqa: BLE001
            return json.dumps({"error": f"error consultando la API del dashboard: {e}"})

    if name == "search_dashboard_charts":
        query = tool_input.get("query", "")
        try:
            return json.dumps(search_dashboard_charts(query), default=_json_default)
        except Exception as e:  # noqa: BLE001
            return json.dumps({"error": f"error buscando en el catalogo: {e}"})

    if name == "get_opportunities_by_sales_lead":
        stage = tool_input.get("stage")
        try:
            return json.dumps(get_opportunities_by_sales_lead(stage), default=_json_default)
        except Exception as e:  # noqa: BLE001
            return json.dumps({"error": f"error consultando la base: {e}"})

    if name == "get_schema_info":
        table = tool_input.get("table")
        try:
            return json.dumps(get_schema_info(table), default=_json_default)
        except Exception as e:  # noqa: BLE001
            return json.dumps({"error": f"error consultando el esquema: {e}"})

    if name == "query_database":
        sql = tool_input.get("sql", "")
        try:
            return json.dumps(run_readonly_query(sql), default=_json_default)
        except Exception as e:  # noqa: BLE001
            return json.dumps({"error": f"error ejecutando la consulta: {e}"})

    if name != "get_metric":
        return json.dumps({"error": f"herramienta desconocida: {name}"})

    metric = tool_input.get("metric")
    month = tool_input.get("month")
    if metric not in METRICS:
        return json.dumps({"error": f"metrica desconocida: {metric}"})

    fn = METRICS[metric]["fn"]
    try:
        no_month = ("pipeline", "replacement_coverage_30d", "clients_multi_candidate")
        result = fn() if metric in no_month else fn(month)
    except Exception as e:  # noqa: BLE001
        return json.dumps({"error": f"error consultando la base: {e}"})

    return json.dumps(result, default=_json_default)


MAX_USER_TURNS = 8  # cuantas preguntas reales (no tool_results) guardamos de historial


def _trim_history(messages: list) -> list:
    """Recorta el historial para no acumular tokens sin limite, cortando siempre
    justo antes de una pregunta real del usuario (nunca en medio de un tool_use/
    tool_result, porque la API rechaza mensajes con ese par incompleto)."""
    user_turns = [
        i for i, m in enumerate(messages) if m["role"] == "user" and isinstance(m["content"], str)
    ]
    if len(user_turns) <= MAX_USER_TURNS:
        return messages
    cutoff = user_turns[-MAX_USER_TURNS]
    return messages[cutoff:]


def ask(question: str, history: list | None = None) -> tuple[str, list]:
    """Responde una pregunta, manteniendo el hilo de la conversacion.
    `history` es la lista de mensajes previa (vacia o None si es la primera vez).
    Devuelve (respuesta_en_texto, historial_actualizado) para que el caller lo
    guarde y se lo pase de nuevo en el siguiente mensaje de esa misma conversacion."""
    messages = list(history) if history else []

    if not messages:
        today = date.today().isoformat()
        content = f"Fecha de hoy: {today}\n\nPregunta: {question}"
    else:
        content = question
    messages.append({"role": "user", "content": content})

    for _ in range(10):  # tope de idas y vueltas con tool use (buscar+traer datos nuevos puede llevar varios pasos)
        try:
            response = client.messages.create(
                model=MODEL,
                max_tokens=4096,  # con preguntas complejas, el modelo puede gastar mucho en "pensar" antes de escribir la respuesta
                system=SYSTEM_PROMPT,
                tools=TOOLS,
                messages=messages,
            )
        except anthropic.APIStatusError as e:
            if "credit balance" in str(e).lower() or "insufficient_quota" in str(e).lower():
                return (
                    "⚠️ Se quedó sin créditos la cuenta de la API de Claude — hay que cargar más "
                    "en console.anthropic.com (Plans & Billing) para que el bot vuelva a funcionar.",
                    _trim_history(messages),
                )
            raise

        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason != "tool_use":
            text = "".join(block.text for block in response.content if block.type == "text").strip()
            if not text:
                text = (
                    "No me salió armar una respuesta en texto para esto (puede ser que se haya "
                    "cortado a mitad de camino). Probá reformular la pregunta o preguntala de nuevo."
                )
            return text, _trim_history(messages)

        tool_results = []
        for block in response.content:
            if block.type == "tool_use":
                result_text = _run_tool(block.name, block.input)
                tool_results.append(
                    {"type": "tool_result", "tool_use_id": block.id, "content": result_text}
                )
        messages.append({"role": "user", "content": tool_results})

    return "No pude terminar de procesar la pregunta, intenta de nuevo o reformulala.", _trim_history(messages)
