from collections.abc import Callable
from typing import Any

TOOLS: dict[str, Callable[..., dict[str, Any]]] = {}
