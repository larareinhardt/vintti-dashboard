from __future__ import annotations

import os

from dotenv import load_dotenv
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

from .agent import ask
from .audit_log import log_event

load_dotenv()

app = App(token=os.environ["SLACK_BOT_TOKEN"])

# Historial de conversacion por canal (DM o canal donde mencionan al bot). En
# memoria del proceso: si se reinicia el bot, arranca de cero para todos.
_conversations: dict[str, list] = {}

# IDs de eventos ya procesados, para no contestar dos veces si Slack reenvia el
# mismo evento (pasa si la conexion se corta un instante o tarda en responder).
# Se recorta cuando crece mucho para no acumular memoria sin limite.
_seen_event_ids: set = set()
_MAX_SEEN_EVENTS = 2000


def _already_processed(event_id: str | None) -> bool:
    if not event_id:
        return False
    if event_id in _seen_event_ids:
        return True
    _seen_event_ids.add(event_id)
    if len(_seen_event_ids) > _MAX_SEEN_EVENTS:
        _seen_event_ids.clear()
    return False


def _with_eyes(client, channel: str, ts: str, fn):
    """Pone una reaccion de ojo mientras `fn` procesa la pregunta, y la saca al
    terminar -- asi se ve que el bot esta laburando en preguntas que tardan."""
    try:
        client.reactions_add(channel=channel, timestamp=ts, name="eyes")
    except Exception:  # noqa: BLE001
        pass
    try:
        return fn()
    finally:
        try:
            client.reactions_remove(channel=channel, timestamp=ts, name="eyes")
        except Exception:  # noqa: BLE001
            pass


@app.event("app_mention")
def handle_mention(event, say, body, client):
    if _already_processed(body.get("event_id")):
        return
    question = event["text"].split(">", 1)[-1].strip()
    thread_ts = event.get("thread_ts") or event.get("ts")
    answer = _with_eyes(client, event["channel"], event["ts"], lambda: _answer(event["channel"], question, event.get("user")))
    _say_safely(say, answer, thread_ts)


@app.event("message")
def handle_dm(event, say, body, client):
    if event.get("channel_type") != "im":
        return
    if event.get("bot_id"):
        return
    if _already_processed(body.get("event_id")):
        return
    thread_ts = event.get("thread_ts")
    question = event.get("text", "")
    answer = _with_eyes(client, event["channel"], event["ts"], lambda: _answer(event["channel"], question, event.get("user")))
    _say_safely(say, answer, thread_ts)


def _say_safely(say, text: str, thread_ts: str | None = None) -> None:
    """Nunca deja el mensaje sin responder: si Slack rechaza el texto (ej: vacio)
    o falla la llamada por cualquier motivo, al menos avisa del error."""
    kwargs = {"thread_ts": thread_ts} if thread_ts else {}
    try:
        say(text or "No tengo una respuesta para eso, intenta reformular la pregunta.", **kwargs)
    except Exception as e:  # noqa: BLE001
        try:
            say(f"Uy, algo fallo mandando la respuesta: {e}", **kwargs)
        except Exception:  # noqa: BLE001
            pass  # si ni esto se puede mandar, no hay mucho mas para hacer aca


def _answer(channel: str, question: str, user: str | None = None) -> str:
    if not question:
        return "Decime que metrica queres entender (ej: '¿por que bajo el NRR este mes?')."
    log_event("pregunta", channel=channel, user=user, texto=question)
    try:
        history = _conversations.get(channel, [])
        text, updated_history = ask(question, history)
        _conversations[channel] = updated_history
        log_event("respuesta", channel=channel, user=user, texto=text)
        return text
    except Exception as e:  # noqa: BLE001
        log_event("error", channel=channel, user=user, error=str(e))
        return f"Uy, algo fallo consultando eso: {e}"


if __name__ == "__main__":
    handler = SocketModeHandler(app, os.environ["SLACK_APP_TOKEN"])
    handler.start()
