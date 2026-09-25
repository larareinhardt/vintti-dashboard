"""Prueba rapida por terminal, sin Slack. Uso: python3 test_cli.py"""

from app.agent import ask

if __name__ == "__main__":
    print("Agente de metricas Vintti (escribi 'salir' para terminar)\n")
    history = []
    while True:
        q = input("Vos: ").strip()
        if q.lower() in {"salir", "exit", "quit"}:
            break
        respuesta, history = ask(q, history)
        print("\nAgente:", respuesta, "\n")
