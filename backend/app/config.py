from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# .env lives at the repository root, one level above backend/.
ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ENV_FILE, extra="ignore")

    database_url: str = "postgresql://atlas:atlas@localhost:5433/atlas"
    redis_url: str = "redis://localhost:6380/0"
    qdrant_url: str = "http://localhost:6333"
    default_budget_usd: float = 1.50
    max_critic_rounds: int = 3

    @property
    def sqlalchemy_url(self) -> str:
        """Use the psycopg 3 driver for SQLAlchemy and Alembic."""
        return self.database_url.replace("postgresql://", "postgresql+psycopg://", 1)


settings = Settings()
