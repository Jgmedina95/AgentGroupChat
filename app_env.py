from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from dotenv import find_dotenv, load_dotenv


@lru_cache(maxsize=1)
def load_environment() -> None:
	dotenv_path = find_dotenv(usecwd=True)
	if dotenv_path:
		load_dotenv(dotenv_path)
		return

	repo_dotenv_path = Path(__file__).resolve().with_name(".env")
	if repo_dotenv_path.exists():
		load_dotenv(repo_dotenv_path)