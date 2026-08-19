import unittest

from robofoot.engine import Simulation, Vec2
from robofoot.engine import rules


class ReconfiguredKickoffTests(unittest.TestCase):
    """Verrouille le bug historique : reset_positions() doit exister et
    replacer les joueurs selon la formation choisie, sans toucher au
    score ni au chrono."""

    def test_reset_positions_applies_chosen_formation(self) -> None:
        sim = Simulation()
        sim.set_formation_preset("red", "4-3-3")
        sim.set_formation_custom(
            "blue", [[97.0, 30.0]] + [[75.0, 10.0 + i * 4.5] for i in range(10)]
        )
        sim.score["red"] = 2  # simule un match en cours
        sim.reset_positions()

        self.assertEqual((sim.players["red_1"].pos.x, sim.players["red_1"].pos.y), (3.0, 30.0))
        self.assertEqual((sim.players["blue_1"].pos.x, sim.players["blue_1"].pos.y), (97.0, 30.0))
        self.assertTrue(sim.players["red_1"].is_goalkeeper)
        self.assertEqual(sim.score["red"], 2)  # score inchangé

    def test_reset_keeps_formation_choice_across_full_reset(self) -> None:
        sim = Simulation()
        sim.set_formation_preset("red", "3-5-2")
        sim.reset()
        self.assertEqual(sim._formation_spec["red"], "3-5-2")


class FormationValidationTests(unittest.TestCase):
    def test_unknown_preset_rejected(self) -> None:
        sim = Simulation()
        with self.assertRaises(ValueError):
            sim.set_formation_preset("red", "formation-inexistante")

    def test_custom_formation_cannot_cross_halfway_line(self) -> None:
        sim = Simulation()
        positions = [[60.0, 30.0]] + [[10.0, 10.0 + i * 4.5] for i in range(10)]
        with self.assertRaises(ValueError):
            sim.set_formation_custom("red", positions)  # red attaque vers x croissant, halfway=50

    def test_custom_formation_wrong_count_rejected(self) -> None:
        sim = Simulation()
        with self.assertRaises(ValueError):
            sim.set_formation_custom("red", [[10.0, 30.0]] * 5)

    def test_invalid_formation_does_not_overwrite_previous_choice(self) -> None:
        sim = Simulation()
        sim.set_formation_preset("red", "3-5-2")
        with self.assertRaises(ValueError):
            sim.set_formation_preset("red", "inexistante")
        self.assertEqual(sim._formation_spec["red"], "3-5-2")


class FoulPenaltyTests(unittest.TestCase):
    def test_foul_freezes_aggressor_and_gives_ball_to_victim(self) -> None:
        sim = Simulation()
        sim.reset_positions()
        a, b = sim.players["red_1"], sim.players["blue_1"]
        a.pos, b.pos = Vec2(50.0, 30.0), Vec2(50.3, 30.0)
        a.vel, b.vel = Vec2(8.0, 0.0), Vec2(0.0, 0.0)  # a (red) percute b (blue) : a = auteur, b = victime

        sim.step()

        self.assertGreater(a.frozen_until_tick, sim.tick)
        self.assertEqual(sim.last_events[0]["type"], "faute")
        self.assertTrue(b.has_ball)   # la victime récupère le ballon...
        self.assertFalse(a.has_ball)  # ...jamais l'auteur
        self.assertAlmostEqual(sim.ball.pos.x, b.pos.x, delta=1.0)  # replacé près d'elle, pas au point de contact

    def test_opponent_cannot_steal_protected_ball_but_teammate_can(self) -> None:
        sim = Simulation()
        sim.reset_positions()
        # isole la zone de contact : écarte tout le monde sauf les 2 protagonistes,
        # pour ne pas mêler des poussées de collision avec le reste de la formation
        for pid, p in sim.players.items():
            if pid not in ("red_1", "blue_1"):
                p.pos = Vec2(2.0, 2.0)
        a, b = sim.players["red_1"], sim.players["blue_1"]
        a.pos, b.pos = Vec2(50.0, 30.0), Vec2(50.3, 30.0)
        a.vel, b.vel = Vec2(8.0, 0.0), Vec2(0.0, 0.0)
        sim.step()
        self.assertEqual(sim.protected_player, "blue_1")
        # isole ce test de la décélération progressive de l'auteur (déjà
        # couverte par test_frozen_player_ignores_new_commands) : sans ça,
        # elle reste momentanément assez rapide pour déclencher une seconde
        # faute involontaire contre la coéquipière approchée plus bas.
        a.vel = Vec2(0.0, 0.0)

        # un attaquant rouge fonce littéralement sur le ballon protégé : ne doit rien changer
        opponent = sim.players["red_2"]
        opponent.pos = Vec2(sim.ball.pos.x, sim.ball.pos.y)
        sim.step()
        self.assertTrue(b.has_ball)
        self.assertFalse(opponent.has_ball)
        self.assertEqual(sim.protected_player, "blue_1")

        # une coéquipière bleue, elle, peut prendre le relais (passe autorisée)
        teammate = sim.players["blue_2"]
        teammate.pos = Vec2(sim.ball.pos.x, sim.ball.pos.y)
        sim.step()
        self.assertTrue(teammate.has_ball)
        self.assertFalse(b.has_ball)

    def test_opponent_shoving_victim_cannot_dislodge_the_ball(self) -> None:
        """Reproduit le vrai bug trouvé en développant cette fonctionnalité :
        un adversaire qui percute physiquement la victime protégée (sans
        toucher le ballon) la repousse hors de la portée normale de
        capture. Sans repli explicite, le ballon devenait alors "libre"
        et l'adversaire pouvait le récupérer l'instant d'après — ce qui
        contournait entièrement la protection."""
        sim = Simulation()
        sim.reset_positions()
        for pid, p in sim.players.items():
            if pid not in ("red_1", "blue_1"):
                p.pos = Vec2(2.0, 2.0)
        a, b = sim.players["red_1"], sim.players["blue_1"]
        a.pos, b.pos = Vec2(50.0, 30.0), Vec2(50.3, 30.0)
        a.vel, b.vel = Vec2(8.0, 0.0), Vec2(0.0, 0.0)
        sim.step()
        a.vel = Vec2(0.0, 0.0)

        opponent = sim.players["red_2"]
        # collé contre la victime (pas contre le ballon) : la pousse par pur contact physique
        opponent.pos = Vec2(b.pos.x - 0.85, b.pos.y)
        for _ in range(6):
            sim.step()
            self.assertTrue(b.has_ball, "la victime ne doit jamais perdre le ballon sous la bousculade")
            self.assertFalse(opponent.has_ball, "l'adversaire ne doit jamais en profiter")

    def test_victim_kick_releases_protection(self) -> None:
        sim = Simulation()
        sim.reset_positions()
        a, b = sim.players["red_1"], sim.players["blue_1"]
        a.pos, b.pos = Vec2(50.0, 30.0), Vec2(50.3, 30.0)
        a.vel, b.vel = Vec2(8.0, 0.0), Vec2(0.0, 0.0)
        sim.step()
        self.assertEqual(sim.protected_player, "blue_1")

        sim.request_kick("blue_1", 1.0, 0.0, power=1.0)
        sim.step()
        self.assertIsNone(sim.protected_player)
        self.assertFalse(b.has_ball)

    def test_protection_expires_if_victim_never_kicks(self) -> None:
        sim = Simulation()
        sim.reset_positions()
        a, b = sim.players["red_1"], sim.players["blue_1"]
        a.pos, b.pos = Vec2(50.0, 30.0), Vec2(50.3, 30.0)
        a.vel, b.vel = Vec2(8.0, 0.0), Vec2(0.0, 0.0)
        sim.step()
        self.assertIsNotNone(sim.protected_player)

        # écarte l'auteur pour ne pas redéclencher une faute quand on avance
        # artificiellement le chrono (sinon son immobilisation, elle aussi
        # basée sur des ticks, serait invalidée par le saut direct)
        a.pos = Vec2(5.0, 5.0)
        a.vel = Vec2(0.0, 0.0)
        sim.tick = sim._protection_expires_tick
        sim.step()
        self.assertIsNone(sim.protected_player)

    def test_frozen_player_ignores_new_commands(self) -> None:
        sim = Simulation()
        sim.reset_positions()
        a, b = sim.players["red_1"], sim.players["blue_1"]
        a.pos, b.pos = Vec2(50.0, 30.0), Vec2(50.3, 30.0)
        a.vel, b.vel = Vec2(8.0, 0.0), Vec2(0.0, 0.0)
        sim.step()

        a.target_vel = Vec2(5.0, 0.0)
        vel_before = a.vel.length()
        sim.step()
        self.assertLess(a.vel.length(), vel_before)  # continue de décélérer, ignore la commande


class GoalLineExitTests(unittest.TestCase):
    def test_attacker_shooting_wide_gives_goal_kick_to_defense(self) -> None:
        sim = Simulation()
        info = rules.check_goal_line_exit(
            sim.fld, _ball_at(sim, x=sim.fld.width, y=sim.fld.height / 2 + 20), last_touch_team="red"
        )
        self.assertEqual(info["kind"], "sortie_de_but")
        self.assertEqual(info["team"], "blue")

    def test_defender_deflecting_behind_gives_corner_to_attack(self) -> None:
        sim = Simulation()
        info = rules.check_goal_line_exit(
            sim.fld, _ball_at(sim, x=sim.fld.width, y=sim.fld.height / 2 + 20), last_touch_team="blue"
        )
        self.assertEqual(info["kind"], "corner")
        self.assertEqual(info["team"], "red")


def _ball_at(sim: Simulation, x: float, y: float):
    sim.ball.pos = Vec2(x, y)
    return sim.ball


if __name__ == "__main__":
    unittest.main()
