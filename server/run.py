"""
Lance le serveur robofoot (Game Controller).

Usage :
    python -m robofoot.server.run
    python -m robofoot.server.run --port 8000 --key-red monequipe1 --key-blue monequipe2
"""

import argparse
import threading
import webbrowser

import uvicorn

from robofoot.server.game_controller import app, controller

LOCAL_HOSTS = ("127.0.0.1", "localhost")


def _open_browser_later(url: str, delay: float = 1.5) -> None:
    """Laisse le temps à uvicorn de démarrer avant d'ouvrir l'onglet."""
    threading.Timer(delay, lambda: webbrowser.open(url)).start()


def main() -> None:
    parser = argparse.ArgumentParser(description="robofoot game controller")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--key-red", default="", help="clé d'accès pour l'équipe rouge")
    parser.add_argument("--key-blue", default="", help="clé d'accès pour l'équipe bleue")
    parser.add_argument(
        "--duration", type=float, default=None,
        help="durée du match en secondes de jeu (défaut : illimité)",
    )
    parser.add_argument(
        "--no-open", action="store_true",
        help="ne pas ouvrir automatiquement le visualiseur (par défaut, il s'ouvre "
             "seulement quand --host est 127.0.0.1/localhost, jamais en 0.0.0.0)",
    )
    args = parser.parse_args()

    controller.set_team_key("red", args.key_red)
    controller.set_team_key("blue", args.key_blue)
    controller.set_duration(args.duration)

    if args.host in LOCAL_HOSTS and not args.no_open:
        _open_browser_later(f"http://{args.host}:{args.port}/viewer")

    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
