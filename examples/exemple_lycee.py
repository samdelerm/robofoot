"""
Exemple niveau lycée : je pilote mes 11 joueurs. Stratégie simple : le
joueur le plus proche du ballon fonce dessus et tire, les autres
reviennent vers leur position de formation.

Utilise `on_update`, appelé automatiquement à chaque nouvelle donnée
reçue du serveur (pas besoin d'écrire sa propre boucle avec sleep()).
"""

import math
import time
import robofoot
from robofoot.engine.formation import home_position
from robofoot.engine import Field

FIELD = Field()


def strategie(client, dt):
    bx, by = client.ball
    mes_joueurs = client.robots[client.team]

    plus_proche_id = min(
        mes_joueurs,
        key=lambda i: math.hypot(mes_joueurs[i].position[0] - bx, mes_joueurs[i].position[1] - by),
    )

    for i, joueur in mes_joueurs.items():
        if joueur.has_ball:
            joueur.kick()
        elif i == plus_proche_id:
            joueur.goto(bx, by, wait=False)
        else:
            hx, hy = home_position(client.team, i, FIELD)
            joueur.goto(hx, hy, wait=False)


with robofoot.Client(host="127.0.0.1", team="blue") as client:
    client.on_update = strategie
    while True:
        time.sleep(1)  # le script principal peut faire autre chose ici (logs, stats...)
