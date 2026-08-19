"""
robofoot.server.game_controller
----------------------------------
Le "Game Controller" : serveur central qui fait tourner la simulation en
continu et sert de pont réseau entre les scripts des compétiteurs et le
terrain. Aucun code de compétiteur ne s'exécute ici — le serveur ne fait
qu'appliquer des commandes bornées (vitesse, tir) reçues par WebSocket.

Protocole WebSocket (JSON), endpoint `/ws` :

  Client -> Serveur
    {"cmd": "hello", "team": "red", "key": ""}
    {"cmd": "control", "robot": "red_1", "vx": 2.0, "vy": 0.0}
    {"cmd": "kick", "robot": "red_1", "dx": 1.0, "dy": 0.0, "power": 1.0, "chandelle": false}

  Serveur -> Client
    {"type": "hello_ok", "team": "red", "constants": {...}}
    {"type": "error", "message": "..."}
    {"type": "state", "tick": ..., "score": {...}, "ball": {...}, "players": {...}}
    (l'état est diffusé en continu à chaque tick, à toutes les connexions)

Une connexion WebSocket ne peut piloter que les robots de l'équipe
annoncée dans son `hello`, avec la bonne clé d'équipe si une clé a été
configurée (`--key-red`, `--key-blue` au lancement du serveur).
"""

from __future__ import annotations
import asyncio
import logging
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from robofoot.engine import Simulation, TICK_HZ, REPLAY_SAMPLE_EVERY, formation

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
VIEWER_HTML_PATH = WEB_DIR / "viewer.html"
VIEWER3D_HTML_PATH = WEB_DIR / "viewer3d.html"
REPLAY_VIEWER_HTML_PATH = Path(__file__).resolve().parent.parent / "runner" / "replay_viewer.html"
ASSETS_DIR = WEB_DIR / "assets"

logger = logging.getLogger("robofoot.server")

MAX_SPEED = 8.5   # borne de sécurité : le serveur ne fait jamais confiance aux valeurs reçues
MIN_GAME_SPEED = 0.25
MAX_GAME_SPEED = 4.0


class GameController:
    def __init__(self) -> None:
        self.sim = Simulation()
        self.team_keys: dict[str, str] = {"red": "", "blue": ""}
        self.admin_key: str = ""
        self.connections: set[WebSocket] = set()
        self._lock = asyncio.Lock()
        self.speed: float = 1.0          # multiplicateur de vitesse de jeu (arbitrage)
        self._speed_accumulator: float = 0.0

    def set_team_key(self, team: str, key: str) -> None:
        self.team_keys[team] = key

    def set_admin_key(self, key: str) -> None:
        self.admin_key = key

    def set_duration(self, duration_s: Optional[float]) -> None:
        self.sim.duration_s = duration_s

    def set_speed(self, speed: float) -> float:
        self.speed = max(MIN_GAME_SPEED, min(MAX_GAME_SPEED, speed))
        return self.speed

    def check_admin_key(self, provided: Optional[str]) -> None:
        if self.admin_key and provided != self.admin_key:
            raise HTTPException(status_code=403, detail="Clé d'arbitrage invalide")

    async def register(self, ws: WebSocket) -> None:
        self.connections.add(ws)

    async def unregister(self, ws: WebSocket) -> None:
        self.connections.discard(ws)

    async def broadcast_loop(self) -> None:
        """Tourne en continu : avance la simulation et diffuse l'état à
        toutes les connexions actives. Tourne même sans aucun client
        connecté (le match ne s'arrête pas). `self.speed` permet à
        l'arbitre d'accélérer/ralentir le déroulement du match : plusieurs
        pas de simulation peuvent être exécutés par image diffusée."""
        period = 1.0 / TICK_HZ
        while True:
            start = asyncio.get_event_loop().time()

            async with self._lock:
                self._speed_accumulator += self.speed
                steps = int(self._speed_accumulator)
                self._speed_accumulator -= steps
                for _ in range(steps):
                    self.sim.step()
                snapshot = self.sim.snapshot()
                snapshot["game_speed"] = self.speed

            dead = []
            for ws in list(self.connections):
                try:
                    await ws.send_json(snapshot)
                except Exception:
                    dead.append(ws)
            for ws in dead:
                self.connections.discard(ws)

            elapsed = asyncio.get_event_loop().time() - start
            await asyncio.sleep(max(0.0, period - elapsed))

    def _validate_robot_ownership(self, team: str, robot: str) -> Optional[str]:
        if robot not in self.sim.players:
            return f"Robot inconnu : {robot}"
        if self.sim.players[robot].team != team:
            return f"Le robot {robot} n'appartient pas à l'équipe {team}"
        return None

    async def handle_control(self, team: str, robot: str, vx: float, vy: float) -> Optional[str]:
        err = self._validate_robot_ownership(team, robot)
        if err:
            return err
        # borne de sécurité côté serveur, quoi que le client envoie
        speed = (vx ** 2 + vy ** 2) ** 0.5
        if speed > MAX_SPEED:
            scale = MAX_SPEED / speed
            vx, vy = vx * scale, vy * scale
        async with self._lock:
            self.sim.set_velocity(robot, vx, vy)
        return None

    async def handle_kick(self, team: str, robot: str, dx: float, dy: float, power: float,
                           chandelle: bool = False) -> Optional[str]:
        err = self._validate_robot_ownership(team, robot)
        if err:
            return err
        async with self._lock:
            self.sim.request_kick(robot, dx, dy, power, chandelle)
        return None

    async def handle_formation(self, team: str, preset: Optional[str], positions: Optional[list]) -> Optional[str]:
        """Choisit la formation de départ de l'équipe `team`. Ne prend
        effet qu'au prochain repositionnement (coup d'envoi, reprise
        après un but). Renvoie un message d'erreur si la formation est
        invalide (nom de preset inconnu, mauvais nombre de joueurs, ou
        joueur au-delà de la ligne médiane) — dans ce cas l'ancienne
        formation reste active."""
        async with self._lock:
            try:
                if positions is not None:
                    self.sim.set_formation_custom(team, positions)
                elif preset is not None:
                    self.sim.set_formation_preset(team, preset)
                else:
                    return "Fournir 'preset' (nom de formation) ou 'positions' (11 coordonnées)"
            except ValueError as exc:
                return str(exc)
        return None


controller = GameController()
app = FastAPI(title="robofoot game controller")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Modèles 3D (robot.glb, etc.) utilisés par /viewer3d — accessibles en
# /assets/<fichier>, sans dépendance externe (contrairement au CDN Three.js).
app.mount("/assets", StaticFiles(directory=str(ASSETS_DIR)), name="assets")


@app.on_event("startup")
async def on_startup() -> None:
    asyncio.create_task(controller.broadcast_loop())


@app.get("/")
@app.get("/viewer")
def get_viewer() -> FileResponse:
    """Sert le visualiseur live 2D : ouvrir http://<serveur>:<port>/viewer
    dans un navigateur suffit, aucune installation nécessaire. Fonctionne
    entièrement hors-ligne (aucune dépendance externe). Servi aussi sur
    "/" pour qu'ouvrir juste l'adresse du serveur fonctionne."""
    return FileResponse(VIEWER_HTML_PATH, media_type="text/html")


@app.get("/viewer3d")
def get_viewer_3d() -> FileResponse:
    """Sert le visualiseur live 3D (Three.js, chargé via CDN — nécessite
    une connexion internet côté navigateur, contrairement à /viewer)."""
    return FileResponse(VIEWER3D_HTML_PATH, media_type="text/html")


@app.get("/replay-viewer")
def get_replay_viewer() -> FileResponse:
    """Sert la page qui charge et rejoue un JSON récupéré sur /replay
    (fichier chargé depuis le disque du navigateur, pas de connexion
    au serveur nécessaire une fois le JSON téléchargé)."""
    return FileResponse(REPLAY_VIEWER_HTML_PATH, media_type="text/html")


@app.get("/constants")
def get_constants() -> dict:
    return controller.sim.constants()


@app.get("/formations")
def get_formations() -> dict:
    """Liste les formations prédéfinies disponibles (pour set_formation)."""
    return {"presets": formation.available_formations(), "max_depth_ratio": formation.MAX_DEPTH_RATIO}


@app.get("/replay")
def get_replay() -> dict:
    """Renvoie l'historique échantillonné (~10 Hz) du match courant : à
    sauvegarder en JSON pour analyse a posteriori, ou à charger dans
    `runner/replay_viewer.html` pour le rejouer hors-ligne. Contient
    aussi les événements complets (buts, fautes, corners...)."""
    return {
        "sample_hz": round(TICK_HZ / REPLAY_SAMPLE_EVERY, 2),
        "field": {
            "width": controller.sim.fld.width,
            "height": controller.sim.fld.height,
            "goal_width": controller.sim.fld.goal_width,
        },
        "frames": controller.sim.replay,
        "events": controller.sim.events,
    }


@app.get("/state")
def get_state() -> dict:
    """Permet à un visualiseur simple (front web) de récupérer l'état par
    polling HTTP, sans WebSocket."""
    snapshot = controller.sim.snapshot()
    snapshot["game_speed"] = controller.speed
    return snapshot


@app.get("/events")
def get_events() -> dict:
    """Historique complet des événements du match (buts, fautes...).
    Utilisé par le viewer pour se mettre à jour s'il se connecte en cours
    de match (le flux WebSocket ne diffuse que les nouveaux événements)."""
    return {"events": controller.sim.events}


@app.post("/admin/reset")
def reset_match(x_admin_key: Optional[str] = Header(default=None)) -> dict:
    """Réinitialise complètement le match : score, chrono, événements."""
    controller.check_admin_key(x_admin_key)
    controller.sim.reset()
    return {"ok": True}


@app.post("/admin/kickoff")
def kickoff(x_admin_key: Optional[str] = Header(default=None)) -> dict:
    """Bouton 'coup d'envoi' de l'arbitre : renvoie tous les robots et le
    ballon à leur position de départ, sans toucher au score ni au chrono."""
    controller.check_admin_key(x_admin_key)
    controller.sim.reset_positions()
    return {"ok": True}


@app.post("/admin/speed")
def set_speed(payload: dict, x_admin_key: Optional[str] = Header(default=None)) -> dict:
    """Change la vitesse de déroulement du match (multiplicateur, ex: 2.0
    = deux fois plus rapide). Bornée entre 0.25x et 4x."""
    controller.check_admin_key(x_admin_key)
    speed = controller.set_speed(float(payload.get("speed", 1.0)))
    return {"ok": True, "speed": speed}


@app.get("/admin/status")
def admin_status() -> dict:
    return {
        "speed": controller.speed,
        "admin_key_required": bool(controller.admin_key),
        "connected_viewers_or_clients": len(controller.connections),
    }


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket) -> None:
    await ws.accept()
    await controller.register(ws)
    team: Optional[str] = None

    try:
        while True:
            msg = await ws.receive_json()
            cmd = msg.get("cmd")

            if cmd == "hello":
                requested_team = msg.get("team")
                key = msg.get("key", "")
                if requested_team not in ("red", "blue"):
                    await ws.send_json({"type": "error", "message": "team doit être 'red' ou 'blue'"})
                    continue
                if controller.team_keys.get(requested_team, "") != key:
                    await ws.send_json({"type": "error", "message": "Clé d'équipe invalide"})
                    continue
                team = requested_team
                await ws.send_json({
                    "type": "hello_ok",
                    "team": team,
                    "constants": controller.sim.constants(),
                })

            elif cmd == "control":
                if team is None:
                    await ws.send_json({"type": "error", "message": "Envoyer 'hello' d'abord"})
                    continue
                err = await controller.handle_control(
                    team, msg.get("robot", ""), float(msg.get("vx", 0.0)), float(msg.get("vy", 0.0)),
                )
                if err:
                    await ws.send_json({"type": "error", "message": err})

            elif cmd == "kick":
                if team is None:
                    await ws.send_json({"type": "error", "message": "Envoyer 'hello' d'abord"})
                    continue
                err = await controller.handle_kick(
                    team, msg.get("robot", ""),
                    float(msg.get("dx", 1.0)), float(msg.get("dy", 0.0)),
                    float(msg.get("power", 1.0)), bool(msg.get("chandelle", False)),
                )
                if err:
                    await ws.send_json({"type": "error", "message": err})

            elif cmd == "formation":
                if team is None:
                    await ws.send_json({"type": "error", "message": "Envoyer 'hello' d'abord"})
                    continue
                err = await controller.handle_formation(team, msg.get("preset"), msg.get("positions"))
                if err:
                    await ws.send_json({"type": "error", "message": err})
                else:
                    await ws.send_json({"type": "formation_ok", "team": team})

            else:
                await ws.send_json({"type": "error", "message": f"Commande inconnue : {cmd}"})

    except WebSocketDisconnect:
        pass
    finally:
        await controller.unregister(ws)