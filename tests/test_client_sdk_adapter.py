import threading
import unittest

from robofoot.client.client import Client, RobotProxy


class DummyClient:
    def __init__(self) -> None:
        self.constants = {}
        self._state = {
            "red_1": {"x": 1.0, "y": 2.0, "vx": 0.0, "vy": 0.0, "has_ball": True, "is_goalkeeper": False},
            "blue_1": {"x": 5.0, "y": 6.0, "vx": 0.0, "vy": 0.0, "has_ball": False, "is_goalkeeper": True},
        }
        self._ball = (3.0, 4.0)
        self._score = {"red": 1, "blue": 0}
        self.sent = []

    def _player_state(self, player_id: str):
        return self._state.get(player_id)

    def _opponent_goal(self):
        return (100.0, 30.0)

    def _send(self, obj):
        self.sent.append(obj)


class ClientSdkAdapterTests(unittest.TestCase):
    def test_robotproxy_college_and_lycee_methods_send_commands(self) -> None:
        client = DummyClient()
        proxy = RobotProxy(client, "red_1", "red")

        proxy.avance_vers(10.0, 20.0)
        proxy.avancer(12.0, 24.0)
        proxy.avance_direction(1.0, -1.0)
        proxy.arreter()
        proxy.tire_vers(20.0, 30.0, puissance=0.8)
        proxy.tirer(21.0, 31.0, puissance=0.9)
        proxy.tire_au_but(puissance=0.5)

        self.assertEqual(client.sent[0]["cmd"], "control")
        self.assertEqual(client.sent[1]["cmd"], "control")
        self.assertEqual(client.sent[2]["cmd"], "control")
        self.assertEqual(client.sent[3]["cmd"], "control")
        self.assertEqual(client.sent[4]["cmd"], "kick")
        self.assertEqual(client.sent[5]["cmd"], "kick")
        self.assertEqual(client.sent[6]["cmd"], "kick")

    def test_client_state_vector_is_flattened(self) -> None:
        client = Client.__new__(Client)
        client._state = {
            "red_1": {"x": 1.0, "y": 2.0, "vx": 0.0, "vy": 0.0, "has_ball": True, "is_goalkeeper": False},
            "blue_1": {"x": 5.0, "y": 6.0, "vx": 0.0, "vy": 0.0, "has_ball": False, "is_goalkeeper": True},
        }
        client._ball = (3.0, 4.0)
        client._score = {"red": 1, "blue": 0}
        client._state_lock = threading.Lock()
        vector = client.state_to_vector()
        self.assertIsNotNone(vector)
        self.assertGreaterEqual(len(vector), 10)


if __name__ == "__main__":
    unittest.main()
