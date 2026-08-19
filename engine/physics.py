"""
robofoot.engine.physics
------------------------
Objets physiques de base : terrain, joueurs, ballon. Physique "arcade"
volontairement simple (pas de moteur externe type Box2D/pymunk), en
coordonnées 2D top-down, en mètres.
"""

from __future__ import annotations
from dataclasses import dataclass, field
import math


@dataclass(frozen=True)
class Field:
    width: float = 100.0
    height: float = 60.0
    goal_width: float = 12.0

    @property
    def goal_y_min(self) -> float:
        return (self.height - self.goal_width) / 2

    @property
    def goal_y_max(self) -> float:
        return (self.height + self.goal_width) / 2

    def is_in_left_goal(self, x: float, y: float) -> bool:
        return x <= 0 and self.goal_y_min <= y <= self.goal_y_max

    def is_in_right_goal(self, x: float, y: float) -> bool:
        return x >= self.width and self.goal_y_min <= y <= self.goal_y_max

    def clamp_x(self, x: float) -> float:
        return max(0.0, min(self.width, x))

    def clamp_y(self, y: float) -> float:
        return max(0.0, min(self.height, y))


@dataclass
class Vec2:
    x: float = 0.0
    y: float = 0.0

    def __add__(self, other: "Vec2") -> "Vec2":
        return Vec2(self.x + other.x, self.y + other.y)

    def __sub__(self, other: "Vec2") -> "Vec2":
        return Vec2(self.x - other.x, self.y - other.y)

    def __mul__(self, scalar: float) -> "Vec2":
        return Vec2(self.x * scalar, self.y * scalar)

    def length(self) -> float:
        return math.hypot(self.x, self.y)

    def normalized(self) -> "Vec2":
        l = self.length()
        if l < 1e-9:
            return Vec2(0.0, 0.0)
        return Vec2(self.x / l, self.y / l)

    def clamped(self, max_length: float) -> "Vec2":
        l = self.length()
        if l <= max_length or l < 1e-9:
            return Vec2(self.x, self.y)
        return self.normalized() * max_length


@dataclass
class Ball:
    pos: Vec2 = field(default_factory=Vec2)
    vel: Vec2 = field(default_factory=Vec2)
    z: float = 0.0
    vz: float = 0.0
    radius: float = 0.3
    friction: float = 0.98

    def step(self, dt: float, fld: Field) -> None:
        self.pos = self.pos + self.vel * dt
        self.vel = self.vel * self.friction

        if self.z > 0.0 or self.vz > 0.0:
            self.z = max(0.0, self.z + self.vz * dt)
            self.vz -= 18.0 * dt
            if self.z == 0.0 and self.vz < 0.0:
                self.vz = 0.0

        if self.pos.y < 0:
            self.pos.y = 0
            self.vel.y *= -0.5
        elif self.pos.y > fld.height:
            self.pos.y = fld.height
            self.vel.y *= -0.5

        if self.pos.x < 0 and not fld.is_in_left_goal(self.pos.x, self.pos.y):
            self.pos.x = 0
            self.vel = Vec2(0.0, 0.0)   # sortie de but : s'arrête net (voir rules.check_goal_line_exit)
        elif self.pos.x > fld.width and not fld.is_in_right_goal(self.pos.x, self.pos.y):
            self.pos.x = fld.width
            self.vel = Vec2(0.0, 0.0)


@dataclass
class Player:
    player_id: str          # ex: "red_1", "blue_7"
    team: str                # "red" ou "blue"
    pos: Vec2 = field(default_factory=Vec2)
    vel: Vec2 = field(default_factory=Vec2)
    # 0.46 m : rayon de collision calé sur le châssis du modèle 3D (demi-
    # largeur 0.43 m, roues comprises jusqu'à ~0.48 m en diagonale — voir
    # web/robot_model.py). La portée de frappe (palette, 0.55 m) est
    # gérée séparément par rules.KICKER_REACH, plus généreuse que le
    # simple cercle de collision.
    radius: float = 0.46
    max_speed: float = 7.0
    max_accel: float = 10.0
    kick_power_max: float = 20.0
    has_ball: bool = False
    is_goalkeeper: bool = False

    # dernière commande de vitesse reçue du client (m/s, repère du terrain)
    target_vel: Vec2 = field(default_factory=Vec2)

    # tick jusqu'auquel ce joueur est immobilisé suite à une faute
    # (voir rules.detect_fouls) ; 0 = jamais gelé
    frozen_until_tick: int = 0

    def step(self, dt: float, fld: Field, tick: int = 0) -> None:
        """Applique la commande de vitesse courante avec une accélération
        bornée (comme un vrai robot ne peut pas changer de vitesse
        instantanément). Si le joueur est gelé suite à une faute (tick <
        frozen_until_tick), il décélère jusqu'à l'arrêt quoi que le
        client envoie comme commande."""
        desired = Vec2(0.0, 0.0) if tick < self.frozen_until_tick else self.target_vel.clamped(self.max_speed)
        delta = desired - self.vel
        delta = delta.clamped(self.max_accel * dt)
        self.vel = self.vel + delta
        self.pos = self.pos + self.vel * dt
        self.pos.x = fld.clamp_x(self.pos.x)
        self.pos.y = fld.clamp_y(self.pos.y)


def resolve_player_collisions(players: list[Player]) -> None:
    n = len(players)
    for i in range(n):
        for j in range(i + 1, n):
            a, b = players[i], players[j]
            delta = b.pos - a.pos
            dist = delta.length()
            min_dist = a.radius + b.radius
            if 0 < dist < min_dist:
                overlap = (min_dist - dist) / 2
                direction = delta.normalized()
                a.pos = a.pos - direction * overlap
                b.pos = b.pos + direction * overlap
