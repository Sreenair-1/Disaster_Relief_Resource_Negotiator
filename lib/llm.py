import json
import os
from collections.abc import Iterable
from typing import Any, TypeVar

from openai import OpenAI
from pydantic import BaseModel

ModelT = TypeVar("ModelT", bound=BaseModel)


class LLMClient:
    def __init__(self, model: str | None = None, retries: int = 2):
        self.model = model or os.getenv("OPENAI_MODEL", "gpt-4o-mini")
        self.retries = retries
        kwargs: dict[str, str] = {"api_key": os.environ["OPENAI_API_KEY"]}
        if os.getenv("OPENAI_BASE_URL"):
            kwargs["base_url"] = os.environ["OPENAI_BASE_URL"]
        self.client = OpenAI(**kwargs)

    def complete_json(self, system: str, user: str, schema: type[ModelT]) -> ModelT:
        prompt = user
        last_error: Exception | None = None
        for attempt in range(self.retries + 1):
            response = self.client.chat.completions.create(
                model=self.model,
                temperature=0,
                response_format={"type": "json_object"},
                messages=[{"role": "system", "content": system}, {"role": "user", "content": prompt}],
            )
            raw = response.choices[0].message.content or ""
            try:
                return schema.model_validate(_parse_json(raw))
            except (json.JSONDecodeError, ValueError, TypeError) as exc:
                last_error = exc
                prompt = f"Repair this into only valid JSON matching the requested schema:\n{raw}"
                if attempt == self.retries:
                    break
        raise ValueError("LLM returned invalid JSON after retries") from last_error


def _parse_json(raw: str) -> Any:
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start >= 0 and end > start:
            return json.loads(cleaned[start : end + 1])
        raise


class StubLLM:
    def __init__(self, responses: Iterable[Any] = ()):
        self.responses = list(responses)

    def complete_json(self, system: str, user: str, schema: type[ModelT]) -> ModelT:
        if not self.responses:
            raise RuntimeError("StubLLM response queue is empty")
        value = self.responses.pop(0)
        if isinstance(value, str):
            value = _parse_json(value)
        return schema.model_validate(value)
