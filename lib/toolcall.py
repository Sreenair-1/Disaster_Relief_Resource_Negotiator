from dataclasses import dataclass
from typing import Any

from lib.schemas import Scenario
from lib.tracing import Tracer
from tools.registry import TOOLS


class ToolError(Exception):
    pass


@dataclass
class ToolResult:
    ok: bool
    data: Any = None
    error: str | None = None


def call_tool(name: str, scenario: Scenario, tracer: Tracer, **kw: Any) -> ToolResult:
    tracer.log("toolcall", name, "tool_requested", {"kwargs": kw})
    try:
        if name not in TOOLS:
            raise ToolError(f"Unknown tool: {name}")
        data = TOOLS[name](scenario, **kw)
        result = ToolResult(ok=True, data=data)
    except ToolError as exc:
        result = ToolResult(ok=False, error=str(exc))
    tracer.log("toolcall", name, "tool_result", result.__dict__)
    return result
