# robofoot

Compétition de football robotique simulé, **11 contre 11**, en Python.
Architecture inspirée du [Robot Soccer Kit](https://robot-soccer-kit.github.io/programming) :
un **serveur central** fait tourner la simulation en continu, et chaque
compétiteur écrit **son propre script Python indépendant** qui s'y
connecte en réseau pour piloter ses joueurs.

**Le code d'un compétiteur ne s'exécute jamais sur le serveur** : le
serveur ne fait qu'appliquer des commandes réseau (vitesse, tir) qu'il
borne lui-même. Conséquence directe : pas besoin de sandboxing pour
ouvrir la compétition à distance, et liberté totale pour l'élève sur la
structure de son programme (boucle simple, threads, callback, RL...).

## Structure

```
robofoot/
  engine/            simulation pure (aucun réseau)
    physics.py          terrain, vecteurs, joueur, ballon
    rules.py            possession, tir, but, fautes, corners/sorties de but
    formation.py        formations prédéfinies + validation de formations personnalisées
    simulation.py       boucle continue temps réel, applique les commandes reçues, replay
  server/            le "Game Controller"
    game_controller.py  FastAPI + WebSocket : diffuse l'état, reçoit les commandes
    run.py               lance le serveur (CLI)
  client/            la bibliothèque que chaque compétiteur importe
    client.py            Client, RobotProxy (control, kick, goto, position...)
    exceptions.py         ClientError
  examples/          scripts d'exemple
    demo_equipe.py       premier script à lancer : équipe complète jouable tout de suite
    exemple_college.py   un seul robot piloté, boucle simple
    exemple_lycee.py     toute l'équipe, via callback on_update
    exemple_bts.py       liberté d'architecture (threads, point d'ancrage ML)
```

## Lancer le serveur

```bash
# depuis le dossier au-dessus de robofoot/
py -m robofoot.server.run --port 8000
```

Options utiles :
- `--host 0.0.0.0` (par défaut) pour accepter des connexions à distance ;
  `127.0.0.1` pour rester purement local.
- `--key-red MOTDEPASSE1 --key-blue MOTDEPASSE2` pour empêcher une équipe
  de piloter les robots adverses pendant une vraie compétition.

L'état du terrain est visible en JSON à tout moment sur `GET /state`,
et les dimensions/règles sur `GET /constants`.

## Écrire son script (côté compétiteur)

```python
import robofoot

with robofoot.Client(host="127.0.0.1", port=8000, team="red") as client:
    client.red_1.control(1.0, 0.0)   # vitesse en m/s, repère du terrain
    client.red_1.kick()               # tire (par défaut, vers le but adverse)
    x, y = client.red_1.position      # position mise à jour en continu
    bx, by = client.ball
```

- `host` : `127.0.0.1` pour jouer en local (même machine ou réseau local
  sans internet), ou l'adresse du serveur de la compétition pour jouer
  à distance.
- `client.red_1` ... `client.red_11`, `client.blue_1` ... `client.blue_11` :
  accès direct à chaque joueur. Un client ne peut piloter que les
  joueurs de son équipe.
- `client.robots["red"][9]` : syntaxe équivalente, pratique pour boucler
  dynamiquement sur toute l'équipe.
- `joueur.goto(x, y, wait=True)` : aide au déplacement (asservissement
  proportionnel simple). `wait=False` pour piloter plusieurs joueurs sans
  threads, en rappelant `goto()` à chaque itération de sa propre boucle.
- `client.on_update = fonction` : callback appelé automatiquement à
  chaque nouvelle donnée reçue du serveur — pratique pour ne pas écrire
  sa propre boucle avec `time.sleep()`.

## Choisir sa formation de départ

```python
client.set_formation(preset="4-3-3")
# ou une formation personnalisée : 11 positions (x, y) absolues,
# la première étant le gardien
client.set_formation(positions=[
    (2.0, 30.0),                                    # gardien
    (12.0, 10.0), (12.0, 25.0), (12.0, 35.0), (12.0, 50.0),  # défenseurs
    (25.0, 15.0), (25.0, 30.0), (25.0, 45.0),                # milieux
    (40.0, 20.0), (40.0, 30.0), (40.0, 40.0),                # attaquants
])
```

- Formations prédéfinies disponibles : `4-4-2`, `4-3-3`, `4-2-3-1`,
  `3-5-2`, `5-3-2` (voir `client.constants["formations_disponibles"]`
  ou `GET /formations`).
- Formation personnalisée : coordonnées absolues du terrain (le même
  repère que `.position`), toujours 11 joueurs, le premier étant
  conventionnellement le gardien. **Contrainte obligatoire : aucun
  joueur ne peut démarrer au-delà de la ligne médiane** (dans son
  propre camp uniquement) — le serveur refuse sinon la formation et
  garde l'ancienne active, avec un message d'erreur expliquant quel
  joueur pose problème.
- Le changement ne prend effet qu'au **prochain repositionnement**
  (début de match, reprise après un but, ou bouton "Coup d'envoi" du
  panneau d'arbitrage) — jamais en pleine action.

## Fautes, corners et sorties de but

Ces règles sont automatiques, rien à faire côté script :

- **Faute** : contact entre deux joueurs d'équipes adverses à vitesse
  relative excessive. L'auteur (le plus rapide des deux) est immobilisé
  ~1,5 s (il décélère jusqu'à l'arrêt, quoi qu'envoie son script). La
  **victime récupère directement le ballon** (placé à portée de sa
  palette, comme en dribble normal) et devient protégée : l'équipe
  adverse ne peut plus l'intercepter — même en bousculant physiquement
  la victime pour tenter de le faire "tomber" — tant qu'elle n'a pas
  elle-même tiré. Ses coéquipiers, eux, peuvent recevoir une passe
  normalement. Filet de sécurité : la protection s'éteint d'elle-même
  après ~15 s si la victime ne tire jamais (script buggé, déconnexion,
  etc.), pour ne pas bloquer le match indéfiniment. Événement diffusé :
  `{"type": "faute", ...}`.
- **Sortie de but** : quand le ballon sort derrière une ligne de but sans
  y entrer, le serveur regarde qui l'a touché en dernier :
  - touché en dernier par l'attaque → **remise en jeu du gardien**
    (`sortie_de_but`), ballon replacé à ~6 m devant sa cage ;
  - touché en dernier par la défense → **corner** pour l'attaque, ballon
    replacé au coin du terrain concerné.

## Replay et analyse a posteriori

Le serveur garde en mémoire un échantillonnage du match courant (~10 Hz,
bien plus léger que les 30 Hz de la diffusion live) :

- `GET /replay` — renvoie `{sample_hz, field, frames, events}` en JSON,
  à tout moment (pendant ou après le match).
- `/replay-viewer` — page de rejeu : charge un JSON récupéré sur
  `/replay` (sauvegardé sur disque, ou collé directement) et permet de
  rejouer l'action image par image, avec scrubbing.

Pratique pour qu'un élève analyse ses matchs d'entraînement hors-ligne,
ou pour garder une trace d'un match de compétition.

## Trois niveaux d'exemples (même bibliothèque, même liberté)

Rien n'est imposé par niveau : ce sont juste des exemples de complexité
croissante dans `examples/`, pour montrer comment démarrer à chaque
niveau scolaire.

- `exemple_college.py` — un seul robot piloté, boucle simple avec
  `time.sleep()`.
- `exemple_lycee.py` — toute l'équipe pilotée via `on_update`.
- `exemple_bts.py` — liberté d'architecture (ici : un thread par
  joueur), avec un point d'ancrage pour brancher un modèle entraîné
  (RL, ML...).

## Tester en local (démarrage rapide)

Terminal 1 :
```bash
python3 -m robofoot.server.run --host 127.0.0.1 --duration 300
```
(`--duration 300` = match de 5 minutes qui se termine tout seul ; sans
cette option, le match tourne indéfiniment)

Terminal 2 :
```bash
PYTHONPATH=. python3 robofoot/examples/demo_equipe.py --team red
```

Terminal 3 :
```bash
PYTHONPATH=. python3 robofoot/examples/demo_equipe.py --team blue
```

`demo_equipe.py` est le premier script à lancer pour voir un vrai match
tout de suite : il pilote les 11 joueurs avec une stratégie simple mais
complète (chasse au ballon, soutien, gardien qui suit latéralement). À
copier/modifier comme point de départ.

## Le visualiseur en direct

Deux versions, au choix :

- **`/viewer`** — 2D, terrain vu du dessus. Fonctionne **entièrement hors-ligne** (aucune dépendance externe), donc à privilégier en salle sans internet.
- **`/viewer3d`** — 3D (Three.js), caméra orbitale (clic-glisser pour tourner, molette pour zoomer), la hauteur du ballon est visible en vrai (chandelles). **Nécessite une connexion internet** côté navigateur (bibliothèque chargée depuis un CDN) — à réserver aux salles connectées.

Les deux affichent : terrain en direct, score et chrono (compte à rebours si `--duration`), barre de possession, fil d'événements horodaté (buts ⚽, fautes 🟨, coups d'envoi ↺), bannière de fin de match.

### Panneau d'arbitrage (icône 🏁, en haut à droite)

- **Coup d'envoi** : renvoie tous les robots et le ballon à leur position de départ, sans toucher au score ni au chrono. Pratique entre deux essais ou après un blocage.
- **Nouveau match** : réinitialisation complète (score, chrono, événements).
- **Vitesse (x0.5 / x1 / x2 / x4)** : accélère ou ralentit le déroulement du match en direct — utile pour enchaîner rapidement des essais pendant les réglages, ou au contraire ralentir pour analyser une action.
- Un champ **clé d'arbitrage** apparaît si le serveur a été lancé avec `--admin-key` (sinon, ces actions sont ouvertes à tout spectateur du viewer — à réserver à un usage en confiance, ex. salle de classe).

## Terrain et physique

Dimensions réglementaires (proportions FIFA) : 105 × 68 m, cage de 7,32 m de large. Robots à 8,5 m/s max, tir jusqu'à 22 m/s. Le multiplicateur de vitesse (`--speed` ou panneau d'arbitrage) accélère le temps de jeu simulé indépendamment de ces valeurs physiques.

## Prochaines étapes possibles (à valider ensemble)

1. **Tournois/matchs organisés** : endpoints pour créer un match entre
   deux équipes précises, minuteur, arrêt automatique en fin de match.
2. **Règles avancées restantes** (hors-jeu, touches sur les côtés) pour
   les niveaux les plus avancés — fautes et sorties de but sont déjà
   gérées (voir plus haut).
3. **Page de classement / ELO** pour la compétition.
4. **Gardien privilégié dans sa surface** (rayon de capture du ballon
   plus grand que les autres joueurs, dans sa propre surface).