"""
Cada funcion calcula UNA metrica de negocio a partir de las tablas crudas de
la base (no hay una app externa que las precalcule, asi que las reconstruimos
aca). Todas devuelven un dict con:
  - "summary": los numeros clave (lo que el agente le muestra al usuario)
  - "detail": filas de detalle (para que el agente pueda explicar el "por que")

Las formulas son un primer approach razonable. Si algun numero no coincide
con lo que ve el equipo en su dashboard interno, se ajusta la formula aca,
en un solo lugar.
"""

from __future__ import annotations

import calendar
from datetime import date, timedelta

from .dashboard_api import get_dashboard_chart
from .db import run_query


def month_bounds(month: str) -> tuple[date, date]:
    """month: 'YYYY-MM' -> (primer dia del mes, ultimo dia del mes)"""
    year, mon = (int(x) for x in month.split("-"))
    start = date(year, mon, 1)
    last_day = calendar.monthrange(year, mon)[1]
    end = date(year, mon, last_day)
    return start, end


def _current_active_cutoff() -> date:
    """
    Vintti considera "activo" a un cliente/candidato con contrato ya firmado que
    arranca dentro de los proximos 30 dias, no solo a los que ya arrancaron hoy
    (coincide con el filtro "Corte 30D" del dashboard interno). Se usa solo para
    saber el estado ACTUAL (ahora mismo), nunca para reconstruir un mes ya cerrado.
    """
    return date.today() + timedelta(days=30)




def _mrr_as_of(end: date) -> dict:
    rows = run_query(
        """
        SELECT
            COALESCE(SUM(ho.fee), 0)       AS mrr_fee,
            COALESCE(SUM(ho.revenue), 0)   AS gmrr,
            COUNT(DISTINCT ho.account_id)   AS active_accounts,
            COUNT(DISTINCT ho.candidate_id) AS active_candidates
        FROM hire_opportunity ho
        JOIN opportunity o ON o.opportunity_id = ho.opportunity_id
        WHERE o.opp_model = 'Staffing'
          AND NULLIF(ho.start_date, '')::date <= %(end)s
          AND (ho.end_date IS NULL OR NULLIF(ho.end_date, '')::date > %(end)s)
        """,
        {"end": end},
    )
    return {"summary": rows[0], "detail": []}


def get_mrr(month: str) -> dict:
    if month == date.today().strftime("%Y-%m"):
        return _mrr_as_of(_current_active_cutoff())
    _, end = month_bounds(month)
    return _mrr_as_of(end)


def get_arpa(month: str) -> dict:
    """ARPA (segun el glosario del dashboard): revenue promedio por cuenta (GMRR / cuentas
    activas), NO el fee de Vintti. Para el fee promedio, ver get_staffing_fee_avg."""
    mrr = get_mrr(month)["summary"]
    accounts = mrr["active_accounts"] or 0
    arpa = (mrr["gmrr"] / accounts) if accounts else 0
    return {"summary": {"arpa": round(arpa, 2), "gmrr": mrr["gmrr"], "active_accounts": accounts}, "detail": []}


def get_arpc(month: str) -> dict:
    """ARPC (segun el glosario del dashboard): revenue promedio por contractor (GMRR /
    candidatos activos), NO el fee de Vintti. Para el fee promedio, ver get_staffing_fee_avg."""
    mrr = get_mrr(month)["summary"]
    candidates = mrr["active_candidates"] or 0
    arpc = (mrr["gmrr"] / candidates) if candidates else 0
    return {"summary": {"arpc": round(arpc, 2), "gmrr": mrr["gmrr"], "active_candidates": candidates}, "detail": []}


def get_staffing_fee_avg(month: str) -> dict:
    """'Staffing fee avg' del dashboard: fee de Vintti (no el revenue total) promedio por
    candidato/contractor activo. Distinto de ARPC, que usa revenue."""
    mrr = get_mrr(month)["summary"]
    candidates = mrr["active_candidates"] or 0
    avg = (mrr["mrr_fee"] / candidates) if candidates else 0
    return {
        "summary": {"staffing_fee_avg": round(avg, 2), "mrr_fee": mrr["mrr_fee"], "active_candidates": candidates},
        "detail": [],
    }


def _new_clients_for_range(start: date, end: date) -> list:
    """Cuentas de Staffing cuya PRIMERA colocacion (en toda su historia) arranco en este rango,
    con el fee de esa primera colocacion (para saber con que fee entraron)."""
    rows = run_query(
        """
        WITH first_start AS (
            SELECT ho.account_id, MIN(NULLIF(ho.start_date, '')::date) AS first_date
            FROM hire_opportunity ho
            JOIN opportunity o ON o.opportunity_id = ho.opportunity_id
            WHERE o.opp_model = 'Staffing'
            GROUP BY ho.account_id
        )
        SELECT fs.account_id, a.client_name, a.account_manager, fs.first_date,
               (SELECT COALESCE(SUM(ho2.fee), 0) FROM hire_opportunity ho2
                JOIN opportunity o2 ON o2.opportunity_id = ho2.opportunity_id
                WHERE ho2.account_id = fs.account_id AND o2.opp_model = 'Staffing'
                  AND NULLIF(ho2.start_date, '')::date = fs.first_date) AS fee_inicial
        FROM first_start fs
        LEFT JOIN account a ON a.account_id = fs.account_id
        WHERE fs.first_date BETWEEN %(start)s AND %(end)s
        ORDER BY fs.first_date
        """,
        {"start": start, "end": end},
    )
    return rows


def _new_candidates_for_range(start: date, end: date) -> list:
    """Candidatos/contractors de Staffing cuya colocacion arranco en este rango, con el fee."""
    rows = run_query(
        """
        SELECT ho.candidate_id, c.name, ho.account_id, a.client_name,
               NULLIF(ho.start_date, '')::date AS start_date, ho.fee
        FROM hire_opportunity ho
        JOIN opportunity o ON o.opportunity_id = ho.opportunity_id
        LEFT JOIN candidates c ON c.candidate_id = ho.candidate_id
        LEFT JOIN account a ON a.account_id = ho.account_id
        WHERE o.opp_model = 'Staffing'
          AND NULLIF(ho.start_date, '')::date BETWEEN %(start)s AND %(end)s
        ORDER BY start_date
        """,
        {"start": start, "end": end},
    )
    return rows


def get_client_churn(month: str) -> dict:
    start, end = month_bounds(month)
    return _client_churn_for_range(start, end)


def _client_churn_for_range(start: date, end: date) -> dict:
    rows = run_query(
        """
        WITH start_active AS (
            SELECT DISTINCT ho.account_id FROM hire_opportunity ho
            JOIN opportunity o ON o.opportunity_id = ho.opportunity_id
            WHERE o.opp_model = 'Staffing'
              AND NULLIF(ho.start_date, '')::date <= %(start)s
              AND (ho.end_date IS NULL OR NULLIF(ho.end_date, '')::date > %(start)s)
        ),
        end_active AS (
            SELECT DISTINCT ho.account_id FROM hire_opportunity ho
            JOIN opportunity o ON o.opportunity_id = ho.opportunity_id
            WHERE o.opp_model = 'Staffing'
              AND NULLIF(ho.start_date, '')::date <= %(end)s
              AND (ho.end_date IS NULL OR NULLIF(ho.end_date, '')::date > %(end)s)
        )
        SELECT s.account_id, a.client_name, a.account_manager
        FROM start_active s
        LEFT JOIN end_active e ON e.account_id = s.account_id
        LEFT JOIN account a ON a.account_id = s.account_id
        WHERE e.account_id IS NULL
        """,
        {"start": start, "end": end},
    )
    start_count_row = run_query(
        """
        SELECT COUNT(DISTINCT ho.account_id) AS n FROM hire_opportunity ho
        JOIN opportunity o ON o.opportunity_id = ho.opportunity_id
        WHERE o.opp_model = 'Staffing'
          AND NULLIF(ho.start_date, '')::date <= %(start)s
          AND (ho.end_date IS NULL OR NULLIF(ho.end_date, '')::date > %(start)s)
        """,
        {"start": start},
    )
    start_count = start_count_row[0]["n"] or 0
    churned = len(rows)
    rate = round((churned / start_count) * 100, 2) if start_count else 0
    return {
        "summary": {"clients_at_month_start": start_count, "clients_churned": churned, "churn_rate_pct": rate},
        "detail": rows,
    }


def get_candidate_churn(month: str) -> dict:
    start, end = month_bounds(month)
    return _candidate_churn_for_range(start, end)


def _candidate_churn_for_range(start: date, end: date) -> dict:
    rows = run_query(
        """
        SELECT ho.candidate_id, c.name, ho.account_id, a.client_name,
               ho.inactive_reason, ho.inactive_comments, ho.fee
        FROM hire_opportunity ho
        JOIN opportunity o ON o.opportunity_id = ho.opportunity_id
        LEFT JOIN candidates c ON c.candidate_id = ho.candidate_id
        LEFT JOIN account a ON a.account_id = ho.account_id
        WHERE o.opp_model = 'Staffing'
          AND NULLIF(ho.end_date, '')::date BETWEEN %(start)s AND %(end)s
        """,
        {"start": start, "end": end},
    )
    start_count_row = run_query(
        """
        SELECT COUNT(DISTINCT ho.candidate_id) AS n FROM hire_opportunity ho
        JOIN opportunity o ON o.opportunity_id = ho.opportunity_id
        WHERE o.opp_model = 'Staffing'
          AND NULLIF(ho.start_date, '')::date <= %(start)s
          AND (ho.end_date IS NULL OR NULLIF(ho.end_date, '')::date > %(start)s)
        """,
        {"start": start},
    )
    start_count = start_count_row[0]["n"] or 0
    churned = len(rows)
    rate = round((churned / start_count) * 100, 2) if start_count else 0
    return {
        "summary": {"candidates_at_month_start": start_count, "candidates_churned": churned, "churn_rate_pct": rate},
        "detail": rows,
    }


def get_nrr_grr(month: str) -> dict:
    """
    NRR/GRR estandar: de los clientes que YA estaban activos al inicio del mes,
    cuanto de su MRR (fee) quedo al final del mes (incluye expansion, contraccion
    y churn; excluye clientes nuevos).
    NRR permite que la expansion compense; GRR la capea en 100% (solo mide lo perdido).
    """
    start, end = month_bounds(month)
    return _nrr_grr_for_range(start, end)


def _nrr_grr_for_range(start: date, end: date) -> dict:
    rows = run_query(
        """
        WITH start_cohort AS (
            SELECT ho.account_id, SUM(ho.fee) AS start_fee
            FROM hire_opportunity ho
            JOIN opportunity o ON o.opportunity_id = ho.opportunity_id
            WHERE o.opp_model = 'Staffing'
              AND NULLIF(ho.start_date, '')::date <= %(start)s
              AND (ho.end_date IS NULL OR NULLIF(ho.end_date, '')::date > %(start)s)
            GROUP BY ho.account_id
        ),
        end_fee AS (
            SELECT ho.account_id, SUM(ho.fee) AS end_fee
            FROM hire_opportunity ho
            JOIN opportunity o ON o.opportunity_id = ho.opportunity_id
            WHERE o.opp_model = 'Staffing'
              AND NULLIF(ho.start_date, '')::date <= %(end)s
              AND (ho.end_date IS NULL OR NULLIF(ho.end_date, '')::date > %(end)s)
            GROUP BY ho.account_id
        )
        SELECT sc.account_id, a.client_name, sc.start_fee, COALESCE(ef.end_fee, 0) AS end_fee
        FROM start_cohort sc
        LEFT JOIN end_fee ef ON ef.account_id = sc.account_id
        LEFT JOIN account a ON a.account_id = sc.account_id
        ORDER BY (sc.start_fee - COALESCE(ef.end_fee, 0)) DESC
        """,
        {"start": start, "end": end},
    )
    total_start = sum(r["start_fee"] or 0 for r in rows)
    total_end = sum(r["end_fee"] or 0 for r in rows)
    total_end_capped = sum(min(r["end_fee"] or 0, r["start_fee"] or 0) for r in rows)
    nrr = round((total_end / total_start) * 100, 2) if total_start else 0
    grr = round((total_end_capped / total_start) * 100, 2) if total_start else 0
    return {
        "summary": {"nrr_pct": nrr, "grr_pct": grr, "start_mrr_fee": total_start, "end_mrr_fee": total_end},
        "detail": rows,
    }


def get_opportunities_by_sales_lead(stage: str | None = None) -> dict:
    """
    Cantidad de oportunidades por sales lead (quien la lleva), opcionalmente
    filtrado por etapa (ej 'Close Win' para saber quien cerro cuantas). Es todo
    el historico, no depende de un mes puntual.
    """
    where = "WHERE o.opp_stage = %(stage)s" if stage else ""
    rows = run_query(
        f"""
        SELECT o.opp_sales_lead, o.opp_model, COUNT(*) AS n, COALESCE(SUM(o.fee), 0) AS fee_total,
               u.user_name
        FROM opportunity o
        LEFT JOIN users u ON u.email_vintti = o.opp_sales_lead
        {where}
        GROUP BY o.opp_sales_lead, o.opp_model, u.user_name
        ORDER BY o.opp_sales_lead, o.opp_model
        """,
        {"stage": stage} if stage else {},
    )
    return {"summary": {"filas": rows}, "detail": rows}


def get_pipeline(_month: str | None = None) -> dict:
    """
    'Active Pipeline' del dashboard: foto actual de las oportunidades abiertas.
    Usa el numero OFICIAL del dashboard (tarjeta gr_kpi_active_pipeline) en vez
    de una formula propia, para coincidir siempre con lo que ve el equipo en
    pantalla. Ojo: la definicion del dashboard incluye ciertas etapas (Sourcing,
    Interviewing, Negotiating, Stop) y excluye otras (Deep Dive, NDA Sent) -- no
    es simplemente "todo lo no cerrado", asi que no reconstruir esto en SQL. No
    depende del mes.
    """
    kpi = get_dashboard_chart("gr_kpi_active_pipeline")["rows"][0]

    # Desglose por etapa/modelo/tipo desde la tabla de detalle del dashboard
    # (misma fuente que el KPI, para que las etapas coincidan con su definicion).
    por_etapa: dict = {}
    por_modelo: dict = {}
    try:
        detalle = get_dashboard_chart("gr_table_active_pipeline_detail")["rows"]
        for r in detalle:
            etapa = r.get("opp_stage") or "Sin etapa"
            modelo = r.get("opp_model") or "Sin modelo"
            tipo = (r.get("opp_type") or "").lower()
            slot = por_etapa.setdefault(
                etapa, {"total": 0, "new": 0, "replacement": 0}
            )
            slot["total"] += 1
            if tipo == "replacement":
                slot["replacement"] += 1
            else:
                slot["new"] += 1
            por_modelo[modelo] = por_modelo.get(modelo, 0) + 1
    except Exception:  # noqa: BLE001 -- el KPI ya trae el total; el detalle es extra
        por_etapa = {}
        por_modelo = {
            "Staffing": kpi.get("pipeline_count_staffing"),
            "Recruiting": kpi.get("pipeline_count_recruiting"),
        }

    return {
        "summary": {
            "total_open_opportunities": kpi.get("pipeline_count"),
            "new": kpi.get("pipeline_count_new"),
            "replacement": kpi.get("pipeline_count_replacement"),
            "por_modelo": por_modelo,
            "total_expected_value": kpi.get("pipeline_revenue"),
            "total_weighted_value": kpi.get("pipeline_revenue_weighted"),
            "win_rate_total_pct": kpi.get("win_rate_total_pct"),
            "corte": kpi.get("corte"),
            "fuente": "dashboard oficial (tarjeta Active Pipeline)",
        },
        "por_etapa": por_etapa,
    }


def get_replacement_coverage_30d() -> dict:
    """
    '% Reemplazos colocados' del dashboard: de las oportunidades de reemplazo
    (opportunity.replacement_of no nulo) que se cerraron en los ultimos 30 dias
    (ganadas o perdidas), que porcentaje se gano (se coloco un reemplazo).
    """
    rows = run_query(
        """
        SELECT opp_stage, COUNT(*) AS n
        FROM opportunity
        WHERE replacement_of IS NOT NULL
          AND opp_close_date >= CURRENT_DATE - INTERVAL '30 days'
          AND opp_stage IN ('Close Win', 'Closed Lost')
        GROUP BY opp_stage
        """
    )
    won = next((r["n"] for r in rows if r["opp_stage"] == "Close Win"), 0)
    lost = next((r["n"] for r in rows if r["opp_stage"] == "Closed Lost"), 0)
    closed = won + lost
    rate = round((won / closed) * 100, 2) if closed else 0
    return {"summary": {"reemplazos_ganados": won, "reemplazos_cerrados": closed, "pct": rate}, "detail": []}


def get_clients_multi_candidate() -> dict:
    """
    '% Clientes con +1 candidato' del dashboard (vista Staffing): de los clientes
    de Staffing activos ahora mismo, que porcentaje tiene mas de un candidato/
    contractor activo al mismo tiempo.
    """
    rows = run_query(
        """
        SELECT ho.account_id, COUNT(DISTINCT ho.candidate_id) AS n_candidatos
        FROM hire_opportunity ho
        JOIN opportunity o ON o.opportunity_id = ho.opportunity_id
        WHERE o.opp_model = 'Staffing'
          AND NULLIF(ho.start_date, '')::date <= %(cutoff)s
          AND (ho.end_date IS NULL OR NULLIF(ho.end_date, '')::date > %(cutoff)s)
        GROUP BY ho.account_id
        """,
        {"cutoff": _current_active_cutoff()},
    )
    total_activos = len(rows)
    con_mas_de_uno = sum(1 for r in rows if r["n_candidatos"] > 1)
    pct = round((con_mas_de_uno / total_activos) * 100, 2) if total_activos else 0
    return {
        "summary": {"clientes_con_mas_de_1_candidato": con_mas_de_uno, "clientes_activos": total_activos, "pct": pct},
        "detail": [],
    }


def _clean_history_row(row: dict) -> dict:
    return {
        "periodo": row.get("periodo"),
        "desde": row.get("period_start"),
        "hasta": row.get("period_end"),
        "active_clients": row.get("active_clients"),
        "active_contractors": row.get("active_contractors"),
        "new_clients": row.get("new_clients"),
        "new_contractors": row.get("new_contractors"),
        "churn_clients": row.get("churn_clients"),
        "churn_contractors": row.get("churn_contractors"),
        "reactivated_clients": row.get("reactivated_clients"),
        "buyout_clients": row.get("buyout_clients"),
        "buyout_contractors": row.get("buyout_contractors"),
        "mrr_fee": row.get("mrr_fee_total"),
        "gmrr": row.get("mrr"),
        "staffing_fee_avg": row.get("staffing_fee_avg"),
    }


def get_weekly_comparison() -> dict:
    """
    Compara la semana actual contra la semana anterior completa, para Staffing.
    Los numeros agregados (MRR, activos, altas, bajas, reactivados, buyouts) vienen
    directo de la tabla "Staffing History" del dashboard via su API (gr_table_staffing_history,
    grain=weekly) -- es la MISMA fuente que usa el dashboard, asi que siempre coincide,
    en vez de que nosotros la recalculemos y quede desalineada.
    Churn M3 y el pipeline activo no tienen una tabla de historial semanal propia, pero
    sus endpoints aceptan un parametro "corte" (fecha) que reconstruye el estado como
    estaba en esa fecha puntual -- se usa para reconstruir ambas semanas igual.
    """
    hist = get_dashboard_chart("gr_table_staffing_history", grain="weekly")
    rows = hist.get("rows", [])
    if len(rows) < 2:
        return {"error": "Todavia no hay suficiente historial semanal guardado para comparar."}

    actual_row, anterior_row = rows[-1], rows[-2]
    this_start = date.fromisoformat(actual_row["period_start"])
    this_end = min(date.fromisoformat(actual_row["period_end"]), date.today())
    last_start = date.fromisoformat(anterior_row["period_start"])
    last_end = date.fromisoformat(anterior_row["period_end"])

    return {
        "actual": _clean_history_row(actual_row),
        "anterior": _clean_history_row(anterior_row),
        "detalle_clientes_caidos_actual": _client_churn_for_range(this_start, this_end)["detail"],
        "detalle_clientes_caidos_anterior": _client_churn_for_range(last_start, last_end)["detail"],
        "detalle_candidatos_caidos_actual": _candidate_churn_for_range(this_start, this_end)["detail"],
        "detalle_candidatos_caidos_anterior": _candidate_churn_for_range(last_start, last_end)["detail"],
        "detalle_clientes_nuevos_actual": _new_clients_for_range(this_start, this_end),
        "detalle_clientes_nuevos_anterior": _new_clients_for_range(last_start, last_end),
        "detalle_candidatos_nuevos_actual": _new_candidates_for_range(this_start, this_end),
        "detalle_candidatos_nuevos_anterior": _new_candidates_for_range(last_start, last_end),
        "nrr_grr": {
            "actual": _nrr_grr_for_range(this_start, this_end)["summary"],
            "anterior": _nrr_grr_for_range(last_start, last_end)["summary"],
        },
        "churn_m3": {
            "actual": get_dashboard_chart("am_kpi_candidate_churn_window", corte=this_end.isoformat())["rows"][0],
            "anterior": get_dashboard_chart("am_kpi_candidate_churn_window", corte=last_end.isoformat())["rows"][0],
        },
        "pipeline": {
            "actual": get_dashboard_chart("gr_kpi_active_pipeline", corte=this_end.isoformat())["rows"][0],
            "anterior": get_dashboard_chart("gr_kpi_active_pipeline", corte=last_end.isoformat())["rows"][0],
        },
    }


METRICS = {
    "mrr": {
        "fn": get_mrr,
        "description": (
            "MRR (fee mensual de Vintti) y GMRR (revenue total: salario + fee) del negocio de "
            "Staffing (ingreso recurrente) activos al cierre del mes. No incluye Recruiting, "
            "que es un negocio de colocaciones puntuales con su propia pestana en el dashboard."
        ),
    },
    "arpa": {
        "fn": get_arpa,
        "description": "ARPA (segun el glosario del dashboard): revenue promedio por cliente activo de Staffing (GMRR / cuentas activas). Incluye salario del candidato, no solo el fee de Vintti.",
    },
    "arpc": {
        "fn": get_arpc,
        "description": "ARPC (segun el glosario del dashboard): revenue promedio por candidato/contractor activo de Staffing (GMRR / candidatos activos). Incluye salario del candidato, no solo el fee de Vintti.",
    },
    "staffing_fee_avg": {
        "fn": get_staffing_fee_avg,
        "description": "'Staffing fee avg' del dashboard: fee de Vintti (NO el revenue total) promedio por candidato/contractor activo. Distinto de ARPC.",
    },
    "client_churn": {
        "fn": get_client_churn,
        "description": "Clientes de Staffing que estaban activos al inicio del mes y quedaron sin colocaciones activas al final (con nombres).",
    },
    "candidate_churn": {
        "fn": get_candidate_churn,
        "description": "Candidatos/contractors de Staffing cuya colocacion termino durante el mes (con razon, si esta cargada).",
    },
    "nrr_grr": {
        "fn": get_nrr_grr,
        "description": "NRR y GRR de Staffing: retencion de ingresos de los clientes que ya estaban activos al inicio del mes (expansion, contraccion, churn).",
    },
    "pipeline": {
        "fn": get_pipeline,
        "description": "Foto actual del pipeline de ventas abierto (oportunidades no cerradas), por etapa y modelo.",
    },
    "replacement_coverage_30d": {
        "fn": get_replacement_coverage_30d,
        "description": (
            "'% Reemplazos colocados' del dashboard: de las oportunidades de reemplazo cerradas "
            "en los ultimos 30 dias, que porcentaje se gano (se coloco un reemplazo exitoso). "
            "Verificado contra el dashboard, no depende de un mes puntual."
        ),
    },
    "clients_multi_candidate": {
        "fn": get_clients_multi_candidate,
        "description": (
            "'% Clientes con +1 candidato' del dashboard (Staffing): de los clientes activos "
            "ahora, que porcentaje tiene mas de un candidato/contractor al mismo tiempo. "
            "Verificado contra el dashboard, no depende de un mes puntual."
        ),
    },
}
