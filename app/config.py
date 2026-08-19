"""
Where the app gets its settings from.

Everything comes out of the .env file in the project root. Nothing secret is
ever written into the source code, so this repo is safe to push to GitHub.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = PROJECT_ROOT / ".env"

# load_dotenv reads .env and puts the values into os.environ.
# Real environment variables win, so CI/production can override the file.
load_dotenv(ENV_FILE, override=False)


class ConfigError(RuntimeError):
    """Raised with a human-readable fix when something is missing."""


@dataclass(frozen=True)
class Settings:
    url: str
    anon_key: str
    bucket: str
    edge_function: str
    app_user: str
    download_dir: Path

    @property
    def functions_base_url(self) -> str:
        """Every Edge Function lives at <project-url>/functions/v1/<name>."""
        return f"{self.url}/functions/v1"

    @property
    def edge_function_url(self) -> str:
        return f"{self.functions_base_url}/{self.edge_function}"


def _require(name: str) -> str:
    value = (os.getenv(name) or "").strip()
    if not value:
        raise ConfigError(
            f"{name} is missing.\n"
            f"  1. Copy .env.example to .env\n"
            f"  2. Fill in {name} from Supabase -> Project Settings -> API\n"
            f"  (looked for the file at: {ENV_FILE})"
        )
    return value


def load_settings() -> Settings:
    """Read and validate the configuration. Raises ConfigError with a fix."""
    url = _require("SUPABASE_URL").rstrip("/")
    if not url.startswith("http"):
        raise ConfigError(
            f"SUPABASE_URL should start with https://, got: {url!r}"
        )

    download_dir = Path(os.getenv("DOWNLOAD_DIR") or "downloads")
    if not download_dir.is_absolute():
        download_dir = PROJECT_ROOT / download_dir

    return Settings(
        url=url,
        anon_key=_require("SUPABASE_ANON_KEY"),
        bucket=(os.getenv("SUPABASE_BUCKET") or "documents").strip(),
        edge_function=(os.getenv("EDGE_FUNCTION_NAME") or "file-guard").strip(),
        app_user=(os.getenv("APP_USER") or "demo_user").strip(),
        download_dir=download_dir,
    )
