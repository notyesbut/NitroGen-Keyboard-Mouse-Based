from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Mapping


class InputController(ABC):
    def __init__(self, dry_run: bool = False) -> None:
        self.dry_run = dry_run

    @abstractmethod
    def step(self, action: Mapping[str, Any]) -> None:
        raise NotImplementedError

    def reset(self) -> None:
        pass

    def close(self) -> None:
        pass
