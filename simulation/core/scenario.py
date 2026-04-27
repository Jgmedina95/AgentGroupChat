from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Generic, Protocol, TypeVar


ConfigT = TypeVar("ConfigT")
ResultT = TypeVar("ResultT")


class ScenarioSpec(Protocol[ConfigT]):
	def to_config(self) -> ConfigT:
		"""Build the executable scenario config from a declarative spec."""
		...

	def to_dict(self) -> dict[str, Any]:
		"""Serialize the declarative scenario spec to primitive data."""
		...

	@classmethod
	def from_dict(cls, payload: dict[str, Any]) -> ScenarioSpec[ConfigT]:
		"""Build the declarative scenario spec from primitive data."""
		...


class JsonScenarioSpec(Generic[ConfigT]):
	@classmethod
	def from_json_file(cls, path: str | Path):
		payload = json.loads(Path(path).read_text(encoding="utf-8"))
		if not isinstance(payload, dict):
			raise ValueError(f"{cls.__name__} file must contain a JSON object")
		return cls.from_dict(payload)


class ScenarioEngine(Protocol[ConfigT, ResultT]):
	def run(self, config: ConfigT) -> ResultT:
		"""Execute a scenario config and return its result."""
		...


def run_scenario_spec(engine: ScenarioEngine[ConfigT, ResultT], spec: ScenarioSpec[ConfigT]) -> ResultT:
	return engine.run(spec.to_config())


__all__ = [
	"ConfigT",
	"JsonScenarioSpec",
	"ResultT",
	"ScenarioEngine",
	"ScenarioSpec",
	"run_scenario_spec",
]