import os
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")
TOKEN = os.getenv("DISCORD_BOT_TOKEN", "")
DATA_PATH = ROOT / os.getenv("DATA_PATH", "data/data.json")
TEST_GUILD_ID = int(os.getenv("TEST_GUILD_ID") or 0)
MISSED_GRACE_SECONDS = int(os.getenv("MISSED_GRACE_SECONDS", "300"))
if MISSED_GRACE_SECONDS < 0:
    raise ValueError("MISSED_GRACE_SECONDS は0以上にしてください。")
