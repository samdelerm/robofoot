"""robofoot : simulation de football robotique 11 vs 11, pilotable en réseau."""

from .client import Client, RobotProxy, ClientError

__all__ = ["Client", "RobotProxy", "ClientError"]
