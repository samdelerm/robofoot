"""
Exemple niveau collège : un seul robot piloté, script très simple.

Idée : je vais chercher le ballon, et si je l'ai, je tire au but.
Lance le serveur d'abord (voir README), puis ce script.
"""

import time
import robofoot

with robofoot.Client(host="127.0.0.1", team="red") as client:
    moi = client.robots[client.team][0]  # on ne prend que le premier robot de l'équipe

    while True:
        bx, by = client.ball
        x, y = moi.position

        if moi.has_ball:
            moi.kick()  # sans direction précisée -> tire automatiquement au but
        else:
            moi.goto(bx, by, wait=False)  # se dirige vers le ballon, sans bloquer

        time.sleep(0.05)
