import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env from project root (one level up from bot/)
load_dotenv(Path(__file__).parent.parent / ".env")

BOT_TOKEN: str = os.environ["BOT_TOKEN"]
OLLAMA_URL: str = os.getenv("OLLAMA_URL", "http://ollama:11434")
OLLAMA_MODEL: str = os.getenv("OLLAMA_MODEL", "phi3.5")
OLLAMA_CHAT_MODEL: str = os.getenv("OLLAMA_CHAT_MODEL", OLLAMA_MODEL)

_raw_ids = os.getenv("ALLOWED_USER_IDS", "").strip()
ALLOWED_USER_IDS: set[int] = (
    {int(uid) for uid in _raw_ids.split(",") if uid.strip()}
    if _raw_ids
    else set()
)

_db_raw = os.getenv("DB_PATH", "/data/app.db")
# Resolve relative paths from project root (parent of this file's directory)
DB_PATH: str = str(
    (Path(__file__).parent.parent / _db_raw).resolve()
    if not Path(_db_raw).is_absolute()
    else _db_raw
)
