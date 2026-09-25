"""
Cliente para la API interna que usa el propio dashboard de Vintti para traer
sus datos. Se descubrio mirando las llamadas de red del dashboard en el
navegador: pide los datos a esta misma direccion, autenticandose con el email
del usuario en un header (no hay clave real de por medio).

Usar esto en vez de reconstruir una formula en SQL cuando sea posible: es mas
rapido, y garantiza que el numero coincide siempre con lo que ve el equipo,
porque es la misma fuente.
"""

import os

import requests
from dotenv import load_dotenv

from .db import run_query

load_dotenv()

BASE_URL = os.environ.get("DASHBOARD_API_BASE", "https://7m6mw95m8y.us-east-2.awsapprunner.com")
USER_EMAIL = os.environ["DASHBOARD_API_EMAIL"]


def search_dashboard_charts(query: str) -> list:
    """
    Busca en la tabla dashboard_charts (la configuracion de las 289 tarjetas de
    todo el dashboard de Vintti) por titulo o dataset_key que contengan `query`.
    Usar esto ANTES de get_dashboard_chart cuando no se conoce de memoria el
    chart_key correcto para lo que se esta preguntando.
    """
    words = [w for w in query.split() if w]
    if not words:
        return []
    conditions = []
    params = {}
    for i, w in enumerate(words):
        key = f"w{i}"
        conditions.append(f"(title ILIKE %({key})s OR dataset_key ILIKE %({key})s)")
        params[key] = f"%{w}%"
    rows = run_query(
        f"""
        SELECT title, chart_key, dataset_key, tab_key, type
        FROM dashboard_charts
        WHERE {' AND '.join(conditions)}
        ORDER BY tab_key, title
        LIMIT 20
        """,
        params,
    )
    return rows


def get_dashboard_chart(chart_key: str, **params) -> dict:
    """Trae los datos de una tarjeta del dashboard por su chart_key (ver la
    tabla dashboard_charts en la base para la lista completa). `params` son
    los filtros opcionales que acepte esa tarjeta (window, grain, subtab,
    opp_stage, etc.) -- si no se pasan, el servidor usa sus valores por
    defecto."""
    url = f"{BASE_URL}/dashboards/main/charts/{chart_key}/data"
    resp = requests.get(
        url,
        params=params,
        headers={"Accept": "application/json", "X-User-Email": USER_EMAIL},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()
