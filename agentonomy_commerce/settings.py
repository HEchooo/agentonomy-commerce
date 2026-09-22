"""Explicit configuration for a single-tenant review service."""
from dataclasses import dataclass
import os
from pathlib import Path
import re


@dataclass(frozen=True)
class Settings:
    state_dir: Path
    api_token: str
    source_commit: str
    project_slug: str
    merchant_port: int = 18081
    requests_per_minute: int = 60

    def __post_init__(self):
        if not re.fullmatch(r"[\x21-\x7e]{32,256}", self.api_token):
            raise ValueError("a 32-256 character private review token is required")
        if not re.fullmatch(r"[0-9a-f]{40}", self.source_commit):
            raise ValueError("an exact 40-character source commit is required")
        if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", self.project_slug) or len(self.project_slug) > 100:
            raise ValueError("invalid project slug")
        if not 1 <= self.merchant_port <= 65535 or not 1 <= self.requests_per_minute <= 600:
            raise ValueError("invalid port or rate limit")
        object.__setattr__(self, "state_dir", Path(self.state_dir).expanduser().resolve())

    @classmethod
    def from_environment(cls):
        return cls(
            state_dir=Path(os.environ.get("AGENTONOMY_STATE_DIR", ".runtime/review")),
            api_token=os.environ.get("AGENTONOMY_API_TOKEN", ""),
            source_commit=os.environ.get("AGENTONOMY_SOURCE_COMMIT", ""),
            project_slug=os.environ.get("AGENTONOMY_PROJECT_SLUG", "hechooo-agentonomy-commerce"),
            merchant_port=int(os.environ.get("AGENTONOMY_MERCHANT_PORT", "18081")),
            requests_per_minute=int(os.environ.get("AGENTONOMY_REQUESTS_PER_MINUTE", "60")),
        )
