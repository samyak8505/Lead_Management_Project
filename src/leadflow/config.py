import os
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("GEMINI_API_KEY")
MODEL = os.getenv("LEADFLOW_MODEL", "gemini-2.5-flash")
MAX_CALLS_PER_RUN = int(os.getenv("LEADFLOW_MAX_CALLS_PER_RUN", "12"))

# Disk cache for LLM calls: re-running evals on unchanged prompts costs 0 API calls.
CACHE_DIR = os.getenv("LEADFLOW_CACHE_DIR", "data/.llm_cache")
USE_CACHE = os.getenv("LEADFLOW_NO_CACHE", "").lower() not in ("1", "true", "yes")


def require_api_key() -> str:
    if not API_KEY or API_KEY == "your-key-here":
        raise RuntimeError("GEMINI_API_KEY missing. Copy .env.example to .env and add your key.")
    return API_KEY
