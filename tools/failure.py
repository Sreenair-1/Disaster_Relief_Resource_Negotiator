import random
import time
from typing import Any


class ToolTimeoutError(Exception):
    """Raised when a tool execution simulates a timeout."""

    pass


def _ensure_tool_timeout_subclass() -> None:
    """Ensure ToolTimeoutError is a subclass of lib.toolcall.ToolError at runtime."""
    global ToolTimeoutError
    try:
        from lib.toolcall import ToolError

        if not issubclass(ToolTimeoutError, ToolError):

            class _BoundToolTimeoutError(ToolError, ToolTimeoutError):
                pass

            _BoundToolTimeoutError.__name__ = "ToolTimeoutError"
            _BoundToolTimeoutError.__qualname__ = "ToolTimeoutError"
            _BoundToolTimeoutError.__doc__ = ToolTimeoutError.__doc__
            ToolTimeoutError = _BoundToolTimeoutError
    except (ImportError, AttributeError):
        pass


_failure_target: str | set[str] | None = None
_failure_mode: str = "error"  # "error", "timeout", "random"
_random_rate: float = 0.5
_random_seed: int | None = None
_timeout_sec: float = 0.0
_timeout_mode_enabled: bool = False
_random_mode_enabled: bool = False


def set_failure(
    name: str | list[str] | set[str] | None = None,
    mode: str | None = None,
    *,
    rate: float = 0.5,
    timeout_sec: float = 0.0,
) -> None:
    """Configure tool failure injection.

    Args:
        name: Tool name to fail, '*' for all tools, 'random' for global random mode,
              'timeout' for global timeout mode, or None to clear failures.
        mode: Failure mode - 'error', 'timeout', or 'random'.
        rate: Probability of failure when mode is 'random' (0.0 to 1.0).
        timeout_sec: Simulated timeout duration in seconds.
    """
    global _failure_target, _failure_mode, _random_rate, _timeout_sec
    global _timeout_mode_enabled, _random_mode_enabled

    _ensure_tool_timeout_subclass()

    # Reset existing flags on new set_failure call
    _timeout_mode_enabled = False
    _random_mode_enabled = False
    _random_rate = rate
    _timeout_sec = timeout_sec

    if name is None:
        _failure_target = None
        _failure_mode = "error"
        return

    if name == "random" or mode == "random":
        _failure_target = "*" if name == "random" else name
        _random_mode_enabled = True
        _failure_mode = "random"
    elif name == "timeout" or mode == "timeout":
        _failure_target = "*" if name == "timeout" else name
        _timeout_mode_enabled = True
        _failure_mode = "timeout"
    else:
        if isinstance(name, (list, set, tuple)):
            _failure_target = set(name)
        else:
            _failure_target = name
        _failure_mode = mode or "error"


def set_random_mode(
    enabled: bool = True,
    rate: float = 0.5,
    seed: int | None = None,
) -> None:
    """Enable or disable random failure mode."""
    global _random_mode_enabled, _random_rate, _random_seed
    _random_mode_enabled = enabled
    _random_rate = rate
    _random_seed = seed
    if seed is not None:
        random.seed(seed)


def set_timeout_mode(
    enabled: bool = True,
    timeout_sec: float = 0.0,
) -> None:
    """Enable or disable timeout simulation mode."""
    global _timeout_mode_enabled, _timeout_sec
    _ensure_tool_timeout_subclass()
    _timeout_mode_enabled = enabled
    _timeout_sec = timeout_sec


def reset_failure() -> None:
    """Reset all failure injection state."""
    set_failure(None)


def is_failing(name: str) -> bool:
    """Check if the given tool is currently set to fail."""
    if _failure_target is None and not _random_mode_enabled and not _timeout_mode_enabled:
        return False

    is_targeted = False
    if _failure_target == "*":
        is_targeted = True
    elif isinstance(_failure_target, (set, list, tuple)):
        is_targeted = name in _failure_target
    elif isinstance(_failure_target, str):
        is_targeted = (_failure_target == name)
    elif _failure_target is None and (_random_mode_enabled or _timeout_mode_enabled):
        is_targeted = True

    if not is_targeted:
        return False

    if _timeout_mode_enabled or _failure_mode == "timeout":
        return True

    if _random_mode_enabled or _failure_mode == "random":
        return random.random() < _random_rate

    return True


def check_failure(name: str) -> None:
    """Raise ToolError if the tool is currently configured to fail."""
    if not is_failing(name):
        return

    _ensure_tool_timeout_subclass()
    from lib.toolcall import ToolError

    if _timeout_mode_enabled or _failure_mode == "timeout":
        if _timeout_sec > 0:
            time.sleep(min(_timeout_sec, 0.05))
        raise ToolTimeoutError(f"Tool '{name}' timed out after {_timeout_sec}s")

    if _random_mode_enabled or _failure_mode == "random":
        raise ToolError(f"Tool '{name}' failed (random failure mode, rate={_random_rate})")

    raise ToolError(f"Tool '{name}' failed: injected failure")
