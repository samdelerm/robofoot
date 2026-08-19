"""
Exemple niveau BTS/prépa : liberté totale sur l'architecture du script.
Ici : un thread indépendant par joueur (chacun avec sa propre logique),
plus un exemple de point d'ancrage pour brancher un modèle entraîné
(RL, arbre de décision, ce que vous voulez).

C'est un exemple parmi d'autres : rien n'oblige à utiliser des threads,
c'est juste pour montrer que la bibliothèque ne l'interdit pas.
"""

import math
import threading
import time
import robofoot


def strategie_attaquant(client, joueur):
    while client._running:
        bx, by = client.ball
        if joueur.has_ball:
            joueur.kick()
        else:
            joueur.goto(bx, by, wait=False)
        time.sleep(0.05)


def strategie_defenseur(client, joueur, x_repli):
    while client._running:
        bx, by = client.ball
        joueur.goto(x_repli, by, wait=False)  # reste aligné en Y avec le ballon
        time.sleep(0.05)


def policy_ml(state_vector: list[float]) -> tuple[float, float]:
    """Point d'ancrage pour un modèle entraîné (RL, réseau de neurones...).
    Reçoit un vecteur d'état et renvoie (vx, vy). Ici : stub aléatoire nul,
    à remplacer par l'inférence d'un vrai modèle (torch, sklearn...)."""
    return (0.0, 0.0)


with robofoot.Client(host="127.0.0.1", team="red") as client:
    attaquants = [client.robots["red"][9], client.robots["red"][10]]
    defenseurs = [(client.robots["red"][2], 15.0), (client.robots["red"][3], 15.0)]

    threads = []
    for j in attaquants:
        t = threading.Thread(target=strategie_attaquant, args=(client, j), daemon=True)
        t.start()
        threads.append(t)
    for j, x in defenseurs:
        t = threading.Thread(target=strategie_defenseur, args=(client, j, x), daemon=True)
        t.start()
        threads.append(t)

    while True:
        time.sleep(1)
