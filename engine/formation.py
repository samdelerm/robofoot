"""
robofoot.engine.formation
---------------------------
Formations de départ : emplacements initiaux des 11 joueurs d'une
équipe. Comprend des formations prédéfinies (4-4-2, 4-3-3, ...) et la
validation de formations personnalisées fournies par un compétiteur.

Convention : dans toute formation (prédéfinie ou personnalisée), le
premier joueur (index 0, "_1") est le gardien.

Règle du coup d'envoi : aucun joueur ne peut démarrer au-delà de la
ligne médiane, dans son propre camp uniquement (`MAX_DEPTH_RATIO`).
Cette règle est appliquée aussi bien aux formations prédéfinies
(conçues pour la respecter) qu'aux formations personnalisées (validées
à l'appel).
"""

from __future__ import annotations
from typing import Union

from .physics import Field, Player, Vec2

# ratio de profondeur (0 = ligne de but, 0.5 = ligne médiane) : aucune
# formation ne peut dépasser cette limite au coup d'envoi.
MAX_DEPTH_RATIO = 0.5

# Chaque formation : liste de 11 tuples (depth_ratio, y_ratio, est_gardien).
# depth_ratio in [0, MAX_DEPTH_RATIO] (distance depuis SA PROPRE ligne de
# but, en fraction de la longueur du terrain), y_ratio in [0, 1].
FORMATIONS: dict[str, list[tuple[float, float, bool]]] = {
    "4-4-2": [
        (0.03, 0.50, True),
        (0.15, 0.15, False), (0.15, 0.38, False), (0.15, 0.62, False), (0.15, 0.85, False),
        (0.32, 0.17, False), (0.32, 0.38, False), (0.32, 0.62, False), (0.32, 0.83, False),
        (0.47, 0.38, False), (0.47, 0.62, False),
    ],
    "4-3-3": [
        (0.03, 0.50, True),
        (0.15, 0.15, False), (0.15, 0.38, False), (0.15, 0.62, False), (0.15, 0.85, False),
        (0.30, 0.25, False), (0.30, 0.50, False), (0.30, 0.75, False),
        (0.47, 0.20, False), (0.47, 0.50, False), (0.47, 0.80, False),
    ],
    "4-2-3-1": [
        (0.03, 0.50, True),
        (0.15, 0.15, False), (0.15, 0.38, False), (0.15, 0.62, False), (0.15, 0.85, False),
        (0.28, 0.35, False), (0.28, 0.65, False),
        (0.40, 0.20, False), (0.40, 0.50, False), (0.40, 0.80, False),
        (0.48, 0.50, False),
    ],
    "3-5-2": [
        (0.03, 0.50, True),
        (0.15, 0.25, False), (0.15, 0.50, False), (0.15, 0.75, False),
        (0.32, 0.10, False), (0.32, 0.30, False), (0.32, 0.50, False), (0.32, 0.70, False), (0.32, 0.90, False),
        (0.47, 0.38, False), (0.47, 0.62, False),
    ],
    "5-3-2": [
        (0.03, 0.50, True),
        (0.15, 0.10, False), (0.15, 0.30, False), (0.15, 0.50, False), (0.15, 0.70, False), (0.15, 0.90, False),
        (0.32, 0.25, False), (0.32, 0.50, False), (0.32, 0.75, False),
        (0.47, 0.38, False), (0.47, 0.62, False),
    ],
}

DEFAULT_FORMATION = "4-4-2"


def available_formations() -> list[str]:
    return sorted(FORMATIONS.keys())


def _team_x(depth: float, team: str, fld: Field) -> float:
    """depth : distance en mètres depuis SA PROPRE ligne de but."""
    return depth if team == "red" else fld.width - depth


def resolve_preset(team: str, fld: Field, name: str) -> list[tuple[float, float, bool]]:
    """Renvoie les 11 positions absolues (x, y, est_gardien) pour une
    formation prédéfinie, orientée selon le camp de l'équipe. Lève
    ValueError si le nom est inconnu."""
    if name not in FORMATIONS:
        raise ValueError(
            f"Formation inconnue : '{name}'. Formations disponibles : "
            f"{', '.join(available_formations())}"
        )
    layout = FORMATIONS[name]
    return [
        (_team_x(depth_ratio * fld.width, team, fld), y_ratio * fld.height, is_gk)
        for depth_ratio, y_ratio, is_gk in layout
    ]


def validate_custom_positions(
    team: str, positions: list, fld: Field
) -> list[tuple[float, float, bool]]:
    """Valide une formation personnalisée fournie par un compétiteur :
    exactement 11 positions (x, y) en coordonnées absolues du terrain
    (le même repère que `client.red_1.position`), la première étant le
    gardien. Lève ValueError si invalide — dans ce cas la formation
    précédente reste active côté serveur."""
    if len(positions) != 11:
        raise ValueError(f"Une formation doit comporter exactement 11 joueurs (reçu : {len(positions)})")

    halfway = fld.width / 2
    resolved: list[tuple[float, float, bool]] = []
    for i, pos in enumerate(positions):
        try:
            x, y = float(pos[0]), float(pos[1])
        except (TypeError, ValueError, IndexError, KeyError):
            raise ValueError(f"Position invalide pour le joueur {i + 1} : {pos!r}")

        if not (0.0 <= x <= fld.width) or not (0.0 <= y <= fld.height):
            raise ValueError(
                f"Position du joueur {i + 1} hors du terrain : ({x}, {y}) "
                f"(terrain : {fld.width} x {fld.height})"
            )

        if team == "red" and x > halfway:
            raise ValueError(
                f"Joueur {i + 1} au-delà de la ligne médiane (x={x:.1f} > {halfway:.1f}) : "
                "une formation de départ doit rester entièrement dans son propre camp"
            )
        if team == "blue" and x < halfway:
            raise ValueError(
                f"Joueur {i + 1} au-delà de la ligne médiane (x={x:.1f} < {halfway:.1f}) : "
                "une formation de départ doit rester entièrement dans son propre camp"
            )

        resolved.append((x, y, i == 0))
    return resolved


def build_players(team: str, layout: list[tuple[float, float, bool]]) -> list[Player]:
    """Construit les 11 Player à partir d'une formation résolue
    (positions absolues, comme renvoyées par `resolve_preset` ou
    `validate_custom_positions`)."""
    players = []
    for i, (x, y, is_gk) in enumerate(layout):
        pid = f"{team}_{i + 1}"
        players.append(Player(player_id=pid, team=team, pos=Vec2(x, y), is_goalkeeper=is_gk))
    return players


def default_formation(team: str, fld: Field) -> list[Player]:
    """Formation 4-4-2 par défaut (rétrocompatibilité)."""
    return build_players(team, resolve_preset(team, fld, DEFAULT_FORMATION))


def home_position(team: str, index: int, fld: Field) -> tuple[float, float]:
    """index : 1..11. Position de la formation 4-4-2 par défaut ;
    utilisé par les scripts d'exemple pour un retour en formation. Ne
    reflète PAS une formation personnalisée choisie via
    `set_formation` (repère fixe, indépendant du serveur)."""
    layout = resolve_preset(team, fld, DEFAULT_FORMATION)
    x, y, _ = layout[index - 1]
    return (x, y)
