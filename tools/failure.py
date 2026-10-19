_failure: str | None = None


def set_failure(name: str | None) -> None:
    global _failure
    _failure = name


def is_failing(name: str) -> bool:
    return _failure is not None and _failure == name
