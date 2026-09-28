"""
Registro simple de auditoria: cada pregunta que le hacen al bot, y cada
herramienta/consulta que usa para responder, queda anotada en un archivo local
(logs/audit.jsonl, una linea de JSON por evento). Sirve para poder revisar
despues "quien pregunto que, y que consulto el bot para contestar" -- util
para supervisar el uso, no solo para debuggear errores.

No se guarda en la base de Vintti (el usuario de la base es de solo lectura,
no podria escribir ahi de todas formas) -- vive como archivo en el server
donde corre el bot.
"""

import json
import os
from datetime import datetime, timezone

LOG_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs", "audit.jsonl")


def log_event(kind: str, **fields) -> None:
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    entry = {"timestamp": datetime.now(timezone.utc).isoformat(), "kind": kind, **fields}
    line = json.dumps(entry, ensure_ascii=False, default=str)

    # Ademas del archivo, lo imprimimos en pantalla (stdout) con flush=True para
    # que quede visible en los logs del server (ej: Railway) sin depender del
    # archivo local, que se pierde en cada redeploy. flush asegura que aparezca
    # al instante y no quede en el buffer de Python.
    try:
        print("[audit] " + line, flush=True)
    except Exception:  # noqa: BLE001
        pass  # el log nunca debe romper una respuesta real al usuario

    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:  # noqa: BLE001
        pass  # el log nunca debe romper una respuesta real al usuario
