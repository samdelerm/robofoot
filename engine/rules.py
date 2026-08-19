"""
robofoot.engine.rules
-----------------------
Règles simplifiées et pédagogiques : possession, tir, but, fautes
(avec sanction réelle : immobilisation de l'auteur + coup franc de
fait), et sorties de but (corner ou remise en jeu du gardien selon le
dernier contact). Le hors-jeu et les touches restent hors-champ pour
l'instant (activables plus tard comme options de tournoi).
"""

from __future__ import annotations
from typing import Optional

from .physics import Ball, Player, Field, Vec2

# Portée de la palette de frappe du modèle 3D (voir web/robot_model.py
# ::KICKER_TIP_REACH ; palette montée sur la face avant du châssis,
# à 0.55 m du centre du robot). Utilisée pour caler les distances de
# jeu (capture, dribble) sur le modèle visuel plutôt que sur des
# valeurs arbitraires.
KICKER_REACH = 0.55
BALL_RADIUS = 0.3  # doit correspondre à physics.Ball.radius

CAPTURE_RADIUS = KICKER_REACH + BALL_RADIUS  # 0.85 : distance à laquelle la palette peut toucher le ballon


def update_possession(ball: Ball, players: list[Player], protected_player_id: Optional[str] = None) -> Optional[str]:
    """Met à jour quel joueur possède le ballon. Renvoie l'équipe du
    joueur qui vient d'obtenir/de garder le ballon (None si personne),
    utilisé pour savoir quelle équipe a touché le ballon en dernier.

    Si `protected_player_id` désigne le porteur actuel (coup franc suite
    à une faute, voir `apply_foul_penalty`), l'équipe adverse est
    entièrement exclue de la recherche du joueur le plus proche : elle
    ne peut ni intercepter le ballon, ni en profiter indirectement en
    bousculant la victime (voir `resolve_player_collisions`) pour la
    repousser hors de portée normale — dans ce cas, elle garde quand
    même le ballon (repli). Ses coéquipiers restent libres de le
    récupérer normalement (passe), comme demandé."""
    if ball.z > 0.2:
        holder = next((p for p in players if p.has_ball), None)
        if holder is not None:
            holder.has_ball = False
        return None

    holder = next((p for p in players if p.has_ball), None)
    carry_distance = KICKER_REACH + ball.radius - 0.08

    protected_team: Optional[str] = None
    if protected_player_id is not None and holder is not None and holder.player_id == protected_player_id:
        protected_team = holder.team

    closest: Optional[Player] = None
    closest_dist = CAPTURE_RADIUS
    for p in players:
        if protected_team is not None and p.team != protected_team:
            continue
        d = (p.pos - ball.pos).length()
        if d < closest_dist:
            closest = p
            closest_dist = d

    if closest is None and protected_team is not None:
        # personne de son équipe à portée normale (typiquement : elle vient
        # d'être bousculée hors de portée) -> elle garde le ballon quand même
        closest = holder

    if closest is not None:
        if holder is not None and holder is not closest:
            holder.has_ball = False
        closest.has_ball = True
        # ballon "collé" contre la palette de frappe (pas juste à sa portée
        # théorique max), pour que le rendu 3D reste cohérent au dribble.
        if closest.vel.length() > 0.1:
            offset = closest.vel.normalized() * carry_distance
        else:
            offset = Vec2(carry_distance if closest.team == "red" else -carry_distance, 0)
        ball.pos = closest.pos + offset
        ball.vel = closest.vel
        ball.z = 0.06
        ball.vz = 0.0
        return closest.team

    if holder is not None:
        holder.has_ball = False
    return None


def apply_kick(ball: Ball, player: Player, dx: float, dy: float, power: float, chandelle: bool = False) -> None:
    direction = Vec2(dx, dy).normalized()
    power = max(0.0, min(1.0, power))
    ball.vel = direction * (player.kick_power_max * power)
    if chandelle:
        ball.z = 0.3
        ball.vz = 8.0 + 4.0 * power
    else:
        ball.z = 0.22
        ball.vz = 1.6 + 0.8 * power
    player.has_ball = False


def check_goal(fld: Field, ball: Ball) -> Optional[str]:
    """Retourne la couleur de l'équipe qui vient de marquer, ou None."""
    if fld.is_in_left_goal(ball.pos.x, ball.pos.y):
        return "blue"
    if fld.is_in_right_goal(ball.pos.x, ball.pos.y):
        return "red"
    return None


def reset_ball_center(fld: Field, ball: Ball) -> None:
    ball.pos = Vec2(fld.width / 2, fld.height / 2)
    ball.vel = Vec2(0, 0)
    ball.z = 0.0
    ball.vz = 0.0


# -- fautes : immobilisation de l'auteur + coup franc de fait ----------------

FOUL_SPEED_THRESHOLD = 5.0    # m/s de vitesse relative de rapprochement, au-delà = faute
FOUL_COOLDOWN_TICKS = 60      # ~2s à 30Hz : évite de spammer la même faute en continu
FOUL_FREEZE_TICKS = 45        # ~1.5s à 30Hz : durée d'immobilisation de l'auteur
FOUL_PROTECTION_TIMEOUT_TICKS = 450  # ~15s à 30Hz : filet de sécurité si la victime ne tire jamais


def detect_fouls(players: list[Player], cooldown: dict, tick: int) -> list[dict]:
    """Contact entre deux joueurs d'équipes adverses à vitesse relative
    excessive = faute. L'auteur (le plus rapide des deux) est immobilisé
    pendant `FOUL_FREEZE_TICKS` (voir Player.step), et la victime reçoit
    directement le ballon en coup franc protégé (voir
    `apply_foul_penalty` et `Simulation.protected_player`) : l'équipe
    adverse ne peut plus l'intercepter tant que la victime ne l'a pas
    joué. Toujours pas de notion de tacle/carton/expulsion —
    volontairement simple."""
    events: list[dict] = []
    n = len(players)
    for i in range(n):
        for j in range(i + 1, n):
            a, b = players[i], players[j]
            if a.team == b.team:
                continue
            delta = b.pos - a.pos
            dist = delta.length()
            min_dist = a.radius + b.radius
            if dist >= min_dist:
                continue
            relative_speed = (a.vel - b.vel).length()
            if relative_speed <= FOUL_SPEED_THRESHOLD:
                continue
            key = frozenset((a.player_id, b.player_id))
            last_tick = cooldown.get(key, -FOUL_COOLDOWN_TICKS - 1)
            if tick - last_tick < FOUL_COOLDOWN_TICKS:
                continue
            cooldown[key] = tick
            aggressor, victim = (a, b) if a.vel.length() >= b.vel.length() else (b, a)
            aggressor.frozen_until_tick = tick + FOUL_FREEZE_TICKS
            aggressor.target_vel = Vec2(0.0, 0.0)
            spot = Vec2((a.pos.x + b.pos.x) / 2, (a.pos.y + b.pos.y) / 2)
            events.append({
                "type": "faute",
                "team": aggressor.team,
                "player": aggressor.player_id,
                "adversaire": victim.player_id,
                "x": spot.x, "y": spot.y,
            })
    return events


def apply_foul_penalty(ball: Ball, players: list[Player], victim: Player) -> None:
    """Sanctionne la faute : le ballon est donné directement à la
    victime (coup franc), placé à portée de sa palette de frappe comme
    lors d'un dribble normal — pas juste lâché au point de contact.
    Reste ensuite protégé pour son équipe via `Simulation.protected_player`
    jusqu'à ce qu'elle le joue (voir `update_possession`)."""
    for p in players:
        p.has_ball = False
    victim.has_ball = True
    carry_distance = KICKER_REACH + ball.radius - 0.08
    facing = Vec2(carry_distance if victim.team == "red" else -carry_distance, 0.0)
    ball.pos = victim.pos + facing
    ball.vel = Vec2(0.0, 0.0)
    ball.z = 0.06
    ball.vz = 0.0


# -- sorties de but : corner ou remise en jeu du gardien ----------------------

GOAL_KICK_DEPTH = 6.0    # distance de la ligne de but pour la remise en jeu (mètres)
CORNER_INSET = 1.0       # distance des deux lignes pour le point de corner (mètres)


def check_goal_line_exit(fld: Field, ball: Ball, last_touch_team: Optional[str]) -> Optional[dict]:
    """Détecte que le ballon vient de s'arrêter derrière une ligne de
    but, en dehors du cadre (cf. Ball.step, qui l'arrête net dans ce
    cas au lieu de le faire rebondir). Renvoie
    {'kind': 'corner' | 'sortie_de_but', 'team': ..., 'side': ...} ou
    None. `side` = le camp dont c'est la ligne de but ; `team` = le
    camp qui bénéficie de la remise en jeu."""
    eps = 1e-6
    if ball.pos.x <= eps and not fld.is_in_left_goal(ball.pos.x, ball.pos.y):
        side = "red"      # ligne de but de l'équipe rouge
    elif ball.pos.x >= fld.width - eps and not fld.is_in_right_goal(ball.pos.x, ball.pos.y):
        side = "blue"
    else:
        return None

    attacking_team = "blue" if side == "red" else "red"
    if last_touch_team == side:
        # dernier contact par l'équipe qui défend cette ligne -> corner pour l'attaque
        return {"kind": "corner", "team": attacking_team, "side": side}
    # dernier contact par l'attaque (tir manqué) -> remise en jeu pour la défense
    return {"kind": "sortie_de_but", "team": side, "side": side}


def restart_spot(fld: Field, kind: str, side: str, exit_y: float) -> Vec2:
    if kind == "corner":
        x = CORNER_INSET if side == "red" else fld.width - CORNER_INSET
        y = CORNER_INSET if exit_y < fld.height / 2 else fld.height - CORNER_INSET
    else:  # sortie_de_but
        x = GOAL_KICK_DEPTH if side == "red" else fld.width - GOAL_KICK_DEPTH
        y = fld.height / 2
    return Vec2(x, y)


def apply_goal_line_restart(ball: Ball, players: list[Player], spot: Vec2) -> None:
    ball.pos = spot
    ball.vel = Vec2(0.0, 0.0)
    ball.z = 0.0
    ball.vz = 0.0
    for p in players:
        p.has_ball = False
