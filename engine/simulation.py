"""
robofoot.engine.simulation
-----------------------------
Simulation qui tourne en continu, en temps réel, indépendamment de qui
est connecté ou non. Elle ne "callback" jamais le code d'un élève : elle
ne fait qu'appliquer les dernières commandes reçues (vitesse, tir,
formation) pour chaque joueur, comme le ferait un vrai robot recevant
des ordres radio.

C'est la pièce centrale que `server/game_controller.py` fait tourner en
tâche de fond et expose via WebSocket. Elle tient aussi l'historique des
événements de match (buts, fautes, corners...) et un replay
échantillonné, pour alimenter un visualiseur de compétition ou une
analyse a posteriori.
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Optional, Union

from .physics import Field, Ball, Vec2, resolve_player_collisions
from . import formation
from . import rules

TICK_HZ = 30.0
DT = 1.0 / TICK_HZ

# le replay n'enregistre pas chaque tick (30Hz) mais 1 tick sur N, pour
# garder un JSON de taille raisonnable sur un match long ; ~10Hz suffit
# largement à rejouer un match de façon fluide.
REPLAY_SAMPLE_EVERY = 3


@dataclass
class KickRequest:
    dx: float
    dy: float
    power: float = 1.0
    chandelle: bool = False


class Simulation:
    def __init__(self, field: Optional[Field] = None, duration_s: Optional[float] = None,
                 formations: Optional[dict[str, Union[str, list]]] = None) -> None:
        self.fld = field or Field()
        self.duration_s = duration_s

        # formation choisie par chaque équipe : soit un nom de preset
        # (str, ex: "4-3-3"), soit une liste de 11 (x, y) personnalisée.
        # Résolue en positions absolues à chaque reset_positions().
        self._formation_spec: dict[str, Union[str, list]] = formations or {
            "red": formation.DEFAULT_FORMATION,
            "blue": formation.DEFAULT_FORMATION,
        }

        self.players: dict = {}
        self.ball = Ball()
        self.score = {"red": 0, "blue": 0}
        self.possession_ticks = {"red": 0, "blue": 0}
        self.tick = 0
        self.finished = False
        self.last_touch_team: Optional[str] = None
        self.protected_player: Optional[str] = None   # coup franc en cours (voir rules.apply_foul_penalty)
        self._protection_expires_tick: Optional[int] = None
        self._pending_kicks: dict[str, KickRequest] = {}
        self._foul_cooldown: dict[frozenset, int] = {}
        self.events: list[dict] = []       # historique complet du match
        self.last_events: list[dict] = []  # événements générés au tick courant seulement
        self.replay: list[dict] = []       # frames échantillonnées, pour analyse/rejouabilité

        self.reset_positions()

    # -- formations -------------------------------------------------------------

    def set_formation_preset(self, team: str, name: str) -> None:
        """Choisit une formation prédéfinie (ex: "4-3-3") pour `team`.
        Prend effet au prochain repositionnement (coup d'envoi, reprise
        après un but, ou bouton 'Coup d'envoi' de l'arbitrage) — pas
        immédiatement en cours de jeu. Lève ValueError si le nom est
        inconnu (la formation précédente reste alors active)."""
        formation.resolve_preset(team, self.fld, name)  # valide (lève si inconnue)
        self._formation_spec[team] = name

    def set_formation_custom(self, team: str, positions: list) -> None:
        """Choisit une formation personnalisée pour `team` : 11
        positions (x, y) absolues (repère du terrain, la première étant
        le gardien). Aucun joueur ne peut dépasser la ligne médiane.
        Lève ValueError si invalide (la formation précédente reste
        alors active)."""
        formation.validate_custom_positions(team, positions, self.fld)  # lève si invalide
        self._formation_spec[team] = list(positions)

    def _resolve_formation(self, team: str) -> list[tuple[float, float, bool]]:
        spec = self._formation_spec[team]
        if isinstance(spec, str):
            return formation.resolve_preset(team, self.fld, spec)
        return formation.validate_custom_positions(team, spec, self.fld)

    # -- commandes reçues des clients (thread-safe car appelées depuis
    #    la boucle asyncio du serveur, jamais concurremment avec step()) --

    def set_velocity(self, player_id: str, vx: float, vy: float) -> None:
        if self.finished:
            return
        p = self.players.get(player_id)
        if p is None:
            raise KeyError(f"Joueur inconnu : {player_id}")
        p.target_vel = Vec2(vx, vy)

    def request_kick(self, player_id: str, dx: float, dy: float, power: float = 1.0,
                     chandelle: bool = False) -> None:
        if self.finished:
            return
        if player_id not in self.players:
            raise KeyError(f"Joueur inconnu : {player_id}")
        self._pending_kicks[player_id] = KickRequest(dx, dy, power, chandelle)

    def reset(self) -> None:
        """Réinitialisation complète : score, chrono, événements,
        replay. Les formations choisies par les équipes sont conservées
        (pas besoin de les redéfinir après un 'Nouveau match')."""
        saved_formations = dict(self._formation_spec)
        self.__init__(self.fld, self.duration_s, formations=saved_formations)

    def reset_positions(self) -> None:
        """Renvoie tous les joueurs et le ballon à leur position de
        départ (formation choisie par chaque équipe), SANS toucher au
        score, au chrono ni à l'historique. Utilisé au démarrage, à la
        reprise après un but, et par le bouton 'Coup d'envoi' de
        l'arbitrage."""
        red_layout = self._resolve_formation("red")
        blue_layout = self._resolve_formation("blue")
        self.players = {
            p.player_id: p
            for p in formation.build_players("red", red_layout) + formation.build_players("blue", blue_layout)
        }
        rules.reset_ball_center(self.fld, self.ball)
        self._pending_kicks.clear()
        self.last_touch_team = None
        self.protected_player = None
        self._protection_expires_tick = None

    # -- un pas de simulation -----------------------------------------------

    def step(self) -> None:
        self.last_events = []

        if self.finished:
            self.tick += 1
            return

        for pid, kr in self._pending_kicks.items():
            p = self.players[pid]
            if p.has_ball:
                rules.apply_kick(self.ball, p, kr.dx, kr.dy, kr.power, kr.chandelle)
                self.last_touch_team = p.team
                if self.protected_player == pid:
                    # la victime a joué le coup franc : le ballon redevient libre pour tous
                    self.protected_player = None
                    self._protection_expires_tick = None
        self._pending_kicks.clear()

        if self.protected_player is not None and self.tick >= self._protection_expires_tick:
            # filet de sécurité : si la victime ne tire jamais, on ne bloque pas le
            # match indéfiniment
            self.protected_player = None
            self._protection_expires_tick = None

        for p in self.players.values():
            p.step(DT, self.fld, self.tick)

        players_list = list(self.players.values())

        foul_events = rules.detect_fouls(players_list, self._foul_cooldown, self.tick)

        resolve_player_collisions(players_list)

        for ev in foul_events:
            # placé après resolve_player_collisions : la victime a pu être
            # légèrement repoussée par le chevauchement au moment du contact,
            # le ballon doit se caler sur sa position finale, pas celle
            # d'avant résolution (sinon il atterrit hors de sa portée).
            victim = self.players[ev["adversaire"]]
            rules.apply_foul_penalty(self.ball, players_list, victim)
            self.protected_player = victim.player_id
            self._protection_expires_tick = self.tick + rules.FOUL_PROTECTION_TIMEOUT_TICKS
            self.last_touch_team = victim.team
            self._record_event(ev)

        self.ball.step(DT, self.fld)

        toucher_team = rules.update_possession(self.ball, players_list, self.protected_player)
        if toucher_team is not None:
            self.last_touch_team = toucher_team

        holder = next((p for p in players_list if p.has_ball), None)
        if holder is not None:
            self.possession_ticks[holder.team] += 1

        scorer = rules.check_goal(self.fld, self.ball)
        if scorer is not None:
            self.score[scorer] += 1
            self._record_event({"type": "but", "team": scorer})
            self.reset_positions()
        else:
            exit_info = rules.check_goal_line_exit(self.fld, self.ball, self.last_touch_team)
            if exit_info is not None:
                spot = rules.restart_spot(self.fld, exit_info["kind"], exit_info["side"], self.ball.pos.y)
                rules.apply_goal_line_restart(self.ball, players_list, spot)
                self.last_touch_team = None
                self.protected_player = None
                self._protection_expires_tick = None
                self._record_event({"type": exit_info["kind"], "team": exit_info["team"]})

        self.tick += 1

        if self.tick % REPLAY_SAMPLE_EVERY == 0:
            self._record_replay_frame()

        if self.duration_s is not None and self.tick * DT >= self.duration_s:
            self._finish_match()

    def _record_event(self, event: dict) -> None:
        event = {**event, "tick": self.tick, "time_s": round(self.tick * DT, 1)}
        self.events.append(event)
        self.last_events.append(event)

    def _record_replay_frame(self) -> None:
        self.replay.append({
            "tick": self.tick,
            "time_s": round(self.tick * DT, 1),
            "score": dict(self.score),
            "ball": {"x": round(self.ball.pos.x, 2), "y": round(self.ball.pos.y, 2), "z": round(self.ball.z, 2)},
            "players": {
                pid: {"team": p.team, "x": round(p.pos.x, 2), "y": round(p.pos.y, 2), "has_ball": p.has_ball}
                for pid, p in self.players.items()
            },
        })

    def _finish_match(self) -> None:
        self.finished = True
        for p in self.players.values():
            p.target_vel = Vec2(0, 0)
            p.vel = Vec2(0, 0)
        self.ball.vel = Vec2(0, 0)
        self._record_event({"type": "fin_de_match", "team": None})

    # -- représentation exportable (JSON) ------------------------------------

    def _possession_pct(self) -> dict:
        total = self.possession_ticks["red"] + self.possession_ticks["blue"]
        if total == 0:
            return {"red": 50.0, "blue": 50.0}
        return {
            "red": round(100 * self.possession_ticks["red"] / total, 1),
            "blue": round(100 * self.possession_ticks["blue"] / total, 1),
        }

    def snapshot(self) -> dict:
        time_s = self.tick * DT
        remaining_s = None
        if self.duration_s is not None:
            remaining_s = max(0.0, self.duration_s - time_s)
        return {
            "type": "state",
            "tick": self.tick,
            "time_s": time_s,
            "remaining_s": remaining_s,
            "finished": self.finished,
            "score": dict(self.score),
            "possession_pct": self._possession_pct(),
            "protected_player": self.protected_player,
            "events": self.last_events,
            "ball": {"x": self.ball.pos.x, "y": self.ball.pos.y,
                      "vx": self.ball.vel.x, "vy": self.ball.vel.y,
                      "z": self.ball.z, "vz": self.ball.vz},
            "players": {
                pid: {
                    "team": p.team, "x": p.pos.x, "y": p.pos.y,
                    "vx": p.vel.x, "vy": p.vel.y,
                    "has_ball": p.has_ball, "is_goalkeeper": p.is_goalkeeper,
                    "frozen": self.tick < p.frozen_until_tick,
                }
                for pid, p in self.players.items()
            },
        }

    def constants(self) -> dict:
        return {
            "field_width": self.fld.width,
            "field_height": self.fld.height,
            "goal_width": self.fld.goal_width,
            "tick_hz": TICK_HZ,
            "max_speed": 7.0,
            "team_size": 11,
            "duration_s": self.duration_s,
            "formations_disponibles": formation.available_formations(),
            "max_depth_ratio": formation.MAX_DEPTH_RATIO,
        }
