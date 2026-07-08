"""Initialize local database."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from modules.db import init_db

if __name__ == "__main__":
    path = init_db()
    print(f"Database initialized: {path}")
