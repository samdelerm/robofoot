"""
Premier programme à lancer sur la compétition.

Contrôle une équipe complète (11 joueurs) avec une stratégie simple mais
vivante : le joueur le plus proche du ballon va le chercher et tire, un
bloc de joueurs suit le ballon en restant à distance (soutien), et le
gardien reste sur sa ligne en suivant le ballon latéralement. Sert de
démo prête à l'emploi et de point de départ à copier/modifier.

Usage :
    # Terminal 1
    python -m robofoot.server.run --host 127.0.0.1

    # Terminal 2
    python examples/demo_equipe.py --team red

    # Terminal 3 (pour voir un vrai match)
    python examples/demo_equipe.py --team blue
"""

import argparse
import math
import time

import robofoot
from robofoot.engine.formation import home_position
from robofoot.engine import Field

FIELD = Field()


def joueur_le_plus_proche(client, ball_pos):
    bx, by = ball_pos
    mes_joueurs = client.robots[client.team]
    return min(
        mes_joueurs.items(),
        key=lambda item: math.hypot(item[1].position[0] - bx, item[1].position[1] - by),
    )[0]


def strategie(client, dt):
    ball = client.ball
    plus_proche = joueur_le_plus_proche(client, ball)

    for i, joueur in client.robots[client.team].items():
        if joueur.is_goalkeeper:
            # reste sur la ligne de but, suit le ballon en Y uniquement
            hx, _ = home_position(client.team, i, FIELD)
            cible_y = max(FIELD.goal_y_min, min(FIELD.goal_y_max, ball[1]))
            joueur.goto(hx, cible_y, wait=False)
            continue

        if joueur.has_ball:
            joueur.kick()  # sans direction précisée -> vise automatiquement le but adverse
        elif i == plus_proche:
            joueur.goto(ball[0], ball[1], wait=False)
        else:
            # position de soutien : formation de base, légèrement tirée vers le ballon
            hx, hy = home_position(client.team, i, FIELD)
            cible_x = hx + (ball[0] - hx) * 0.25
            cible_y = hy + (ball[1] - hy) * 0.25
            joueur.goto(cible_x, cible_y, wait=False)


def main() -> None:
    parser = argparse.ArgumentParser(description="Équipe de démo robofoot")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--team", choices=["red", "blue"], required=True)
    parser.add_argument("--key", default="")
    args = parser.parse_args()

    with robofoot.Client(host=args.host, port=args.port, team=args.team, key=args.key) as client:
        print(f"Connecté en tant qu'équipe {args.team}. Ctrl+C pour arrêter.")

        # Formation de départ : soit un preset ("4-4-2", "4-3-3", "4-2-3-1",
        # "3-5-2", "5-3-2" — voir client.constants["formations_disponibles"]),
        # soit 11 positions (x, y) personnalisées (le 1er = le gardien),
        # à condition de rester dans son propre camp (pas de dépassement de
        # la ligne médiane). Prend effet au prochain coup d'envoi.
        client.set_formation(preset="4-3-3")

        client.on_update = strategie
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print("Arrêt du script.")


if __name__ == "__main__":
    main()
