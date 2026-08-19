"""
robofoot.client
------------------
Bibliothèque que chaque compétiteur importe dans SON PROPRE script pour
piloter ses joueurs. Le script tourne entièrement chez le compétiteur
(sa machine) : le serveur ne fait qu'appliquer les commandes envoyées ici.

Inspiré directement de l'API du Robot Soccer Kit :
https://robot-soccer-kit.github.io/programming

Exemple minimal :

    import robofoot

    with robofoot.Client(host="127.0.0.1", team="red") as client:
        client.red_1.control(1.0, 0.0)   # avance à 1 m/s vers x croissant
        print(client.ball)

Chaque compétiteur écrit ce qu'il veut autour de cet appel : boucle
simple, threads, machine learning... la bibliothèque n'impose rien.
"""

from __future__ import annotations
import json
import math
import threading
import time
from typing import Callable, Optional

import websocket  # package "websocket-client"

from .exceptions import ClientError

DEFAULT_PORT = 8000
GOTO_POLL_HZ = 20.0


class RobotProxy:
    """Représente UN joueur que le client est autorisé à piloter."""

    def __init__(self, client: "Client", player_id: str, team: str) -> None:
        self._client = client
        self.player_id = player_id
        self.team = team

    # -- lecture d'état (mis à jour en continu en tâche de fond) ------------

    @property
    def position(self) -> tuple[float, float]:
        s = self._client._player_state(self.player_id)
        return (s["x"], s["y"]) if s else (0.0, 0.0)

    @property
    def velocity(self) -> tuple[float, float]:
        s = self._client._player_state(self.player_id)
        return (s["vx"], s["vy"]) if s else (0.0, 0.0)

    @property
    def has_ball(self) -> bool:
        s = self._client._player_state(self.player_id)
        return bool(s and s["has_ball"])

    @property
    def is_goalkeeper(self) -> bool:
        s = self._client._player_state(self.player_id)
        return bool(s and s["is_goalkeeper"])

    # -- commandes ------------------------------------------------------------

    def _send_velocity(self, vx: float, vy: float) -> None:
        max_speed = self._client.constants.get("max_speed", 7.0)
        vx = max(-max_speed, min(max_speed, vx))
        vy = max(-max_speed, min(max_speed, vy))
        self.control(vx, vy)

    def control(self, vx: float, vy: float) -> None:
        """Fixe la vitesse du joueur, en m/s, dans le repère du terrain."""
        self._client._send({"cmd": "control", "robot": self.player_id, "vx": vx, "vy": vy})

    def avance_vers(self, x: float, y: float) -> None:
        """Compatibilité collège : envoie le joueur vers le point (x, y)."""
        px, py = self.position
        dx, dy = x - px, y - py
        self._send_velocity(dx * 2.0, dy * 2.0)

    def avance_direction(self, dx: float, dy: float) -> None:
        """Compatibilité collège/lycée : envoie une consigne de déplacement brute."""
        self._send_velocity(dx, dy)

    def avancer(self, x: float, y: float) -> None:
        """Compatibilité lycée : nom simplifié de avance_vers."""
        self.avance_vers(x, y)

    def arreter(self) -> None:
        """Compatibilité lycée : stoppe le robot."""
        self.stop()

    def j_ai_le_ballon(self) -> bool:
        """Compatibilité collège : indique si le robot possède le ballon."""
        return self.has_ball

    def tire_vers(self, x: float, y: float, puissance: float = 1.0,
                  chandelle: bool = False) -> None:
        """Compatibilité collège : tire vers un point donné si le robot a le ballon."""
        if not self.has_ball:
            return
        px, py = self.position
        self.kick(x - px, y - py, power=puissance, chandelle=chandelle)

    def tirer(self, x: float, y: float, puissance: float = 1.0,
              chandelle: bool = False) -> None:
        """Compatibilité lycée : nom simplifié de tire_vers."""
        self.tire_vers(x, y, puissance, chandelle=chandelle)

    def tire_au_but(self, puissance: float = 1.0, chandelle: bool = False) -> None:
        """Compatibilité collège : tire vers le but adverse."""
        gx, gy = self._client._opponent_goal()
        self.tire_vers(gx, gy, puissance, chandelle=chandelle)

    def stop(self) -> None:
        self.control(0.0, 0.0)

    def kick(self, dx: Optional[float] = None, dy: Optional[float] = None, power: float = 1.0,
             chandelle: bool = False) -> None:
        """Tire le ballon. Sans direction fournie, tire automatiquement
        vers le but adverse (comme le kicker fixe d'un vrai robot RSK)."""
        if dx is None or dy is None:
            gx, gy = self._client._opponent_goal()
            x, y = self.position
            dx, dy = gx - x, gy - y
        self._client._send({"cmd": "kick", "robot": self.player_id, "dx": dx, "dy": dy, "power": power,
                            "chandelle": chandelle})

    def goto(self, x: float, y: float, wait: bool = True, tolerance: float = 0.3) -> bool:
        """Envoie le joueur vers la position (x, y) avec un contrôleur
        proportionnel simple. Si wait=True (défaut), bloque jusqu'à
        l'arrivée. Si wait=False, envoie une seule commande de vitesse et
        renvoie immédiatement True/False (arrivé ou non) — pratique pour
        piloter plusieurs joueurs "en parallèle" sans threads, comme chez RSK."""
        max_speed = self._client.constants.get("max_speed", 7.0)

        def _one_step() -> bool:
            px, py = self.position
            dx, dy = x - px, y - py
            dist = math.hypot(dx, dy)
            if dist < tolerance:
                self.stop()
                return True
            gain = 2.0
            vx = max(-max_speed, min(max_speed, dx * gain))
            vy = max(-max_speed, min(max_speed, dy * gain))
            self.control(vx, vy)
            return False

        if not wait:
            return _one_step()

        while True:
            if _one_step():
                return True
            time.sleep(1.0 / GOTO_POLL_HZ)


class Client:
    def __init__(self, host: str = "127.0.0.1", port: int = DEFAULT_PORT,
                 team: str = "red", key: str = "", timeout: float = 5.0) -> None:
        if team not in ("red", "blue"):
            raise ClientError("team doit être 'red' ou 'blue'")

        self.host, self.port, self.team = host, port, team
        self.constants: dict = {}
        self.on_update: Optional[Callable[["Client", float], None]] = None

        self._state: dict = {}
        self._ball = (0.0, 0.0)
        self._score = {"red": 0, "blue": 0}
        self._state_lock = threading.Lock()
        self._send_lock = threading.Lock()
        self._running = True
        self._hello_error: Optional[str] = None
        self._connected_event = threading.Event()

        try:
            self._ws = websocket.create_connection(f"ws://{host}:{port}/ws", timeout=timeout)
        except Exception as exc:
            raise ClientError(f"Impossible de se connecter à {host}:{port} ({exc})") from exc

        self._thread = threading.Thread(target=self._recv_loop, daemon=True)
        self._thread.start()

        self._send({"cmd": "hello", "team": team, "key": key})
        if not self._connected_event.wait(timeout=timeout):
            self.close()
            raise ClientError("Le serveur n'a pas répondu au 'hello' (timeout)")
        if self._hello_error:
            self.close()
            raise ClientError(self._hello_error)

        # Proxies dynamiques : client.red_1 .. client.red_11, client.blue_1 .. client.blue_11
        self.robots: dict[str, dict[int, RobotProxy]] = {"red": {}, "blue": {}}
        for t in ("red", "blue"):
            for i in range(1, 12):
                pid = f"{t}_{i}"
                proxy = RobotProxy(self, pid, t)
                setattr(self, pid, proxy)
                self.robots[t][i] = proxy

    # -- context manager -----------------------------------------------------

    def __enter__(self) -> "Client":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    def close(self) -> None:
        for i in range(1, 12):
            try:
                self.robots[self.team][i].stop()
            except Exception:
                pass
        self._running = False
        try:
            self._ws.close()
        except Exception:
            pass

    # -- état lu par les RobotProxy et par l'utilisateur -----------------------

    @property
    def ball(self) -> tuple[float, float]:
        with self._state_lock:
            return self._ball

    @property
    def score(self) -> dict:
        with self._state_lock:
            return dict(self._score)

    def set_formation(self, preset: Optional[str] = None,
                       positions: Optional[list[tuple[float, float]]] = None) -> None:
        """Choisit la formation de départ de son équipe : soit un nom de
        formation prédéfinie (ex: "4-3-3" — voir `client.constants['formations_disponibles']`
        ou `GET /formations`), soit 11 positions (x, y) personnalisées
        en coordonnées absolues du terrain (le même repère que
        `.position`), la première étant le gardien.

        Aucun joueur ne peut démarrer au-delà de la ligne médiane : le
        serveur refuse sinon la formation (et garde l'ancienne). Le
        changement ne prend effet qu'au prochain repositionnement
        (début de match, reprise après un but, ou bouton 'Coup
        d'envoi' du panneau d'arbitrage) — pas immédiatement en cours
        de jeu. Une erreur éventuelle (nom inconnu, formation invalide)
        est affichée sur stderr, comme pour les autres commandes."""
        if preset is None and positions is None:
            raise ClientError("set_formation : fournir preset=... ou positions=...")
        payload: dict = {"cmd": "formation", "team": self.team}
        if preset is not None:
            payload["preset"] = preset
        if positions is not None:
            payload["positions"] = [[float(x), float(y)] for x, y in positions]
        self._send(payload)

    def state_to_vector(self) -> list[float]:
        """Compatibilité BTS : aplati l'état courant du client en vecteur."""
        with self._state_lock:
            players = dict(self._state)
            score = dict(self._score)
            ball_x, ball_y = self._ball

        vec: list[float] = [float(ball_x), float(ball_y), 0.0, 0.0]
        vec += [float(score.get("red", 0)), float(score.get("blue", 0))]
        for player_id in sorted(players):
            s = players[player_id]
            vec += [
                float(s.get("x", 0.0)),
                float(s.get("y", 0.0)),
                float(s.get("vx", 0.0)),
                float(s.get("vy", 0.0)),
                1.0 if s.get("has_ball", False) else 0.0,
            ]
        return vec

    def _player_state(self, player_id: str) -> Optional[dict]:
        with self._state_lock:
            return self._state.get(player_id)

    def _opponent_goal(self) -> tuple[float, float]:
        fw = self.constants.get("field_width", 100.0)
        fh = self.constants.get("field_height", 60.0)
        return (fw, fh / 2) if self.team == "red" else (0.0, fh / 2)

    # -- réseau interne ---------------------------------------------------------

    def _send(self, obj: dict) -> None:
        with self._send_lock:
            try:
                self._ws.send(json.dumps(obj))
            except Exception as exc:
                raise ClientError(f"Échec d'envoi au serveur : {exc}") from exc

    def _recv_loop(self) -> None:
        while self._running:
            try:
                raw = self._ws.recv()
            except Exception:
                break
            if not raw:
                continue
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue

            mtype = msg.get("type")
            if mtype == "hello_ok":
                self.constants = msg["constants"]
                self._connected_event.set()
            elif mtype == "error":
                if not self._connected_event.is_set():
                    self._hello_error = msg.get("message", "erreur inconnue")
                    self._connected_event.set()
                else:
                    # erreur sur une commande envoyée après coup (ex: robot
                    # inconnu) : affichée mais ne casse pas le script de l'élève
                    print(f"[robofoot] Erreur serveur : {msg.get('message')}",
                          file=__import__("sys").stderr)
            elif mtype == "state":
                with self._state_lock:
                    self._state = msg["players"]
                    self._ball = (msg["ball"]["x"], msg["ball"]["y"])
                    self._score = msg["score"]
                if self.on_update is not None:
                    try:
                        self.on_update(self, 1.0 / self.constants.get("tick_hz", 30.0))
                    except Exception:
                        import traceback
                        print("Erreur dans on_update :", file=__import__("sys").stderr)
                        traceback.print_exc()
