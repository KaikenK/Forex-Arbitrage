import uvicorn

# Load .env before importing the app: backend.config reads os.environ at import
# time (ARBEX_DATA_MODE, DHAN_CLIENT_ID / DHAN_ACCESS_TOKEN, etc.).
try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:
    pass

from backend.server import main


if __name__ == "__main__":
    uvicorn.run(main.app, host="0.0.0.0", port=8000, log_level="info")
