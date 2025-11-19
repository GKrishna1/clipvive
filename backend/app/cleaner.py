# backend/app/cleaner.py
"""
Cleaner loop — runs cleanup_local_storage() periodically.
This file intentionally small and explicit so the container can simply run:
    python3 -u app/cleaner.py
"""

import time
from app.tasks import cleanup_local_storage

# Run cleanup every 24 hours by default (in seconds)
SLEEP_SECONDS = int(__import__("os").environ.get("CLEANER_SLEEP_SECONDS", 24 * 3600))

if __name__ == "__main__":
    while True:
        try:
            res = cleanup_local_storage()
            print("cleanup result:", res)
        except Exception as e:
            # Never crash the loop: log and continue
            print("cleanup exception:", repr(e))
        # Sleep until next run
        time.sleep(SLEEP_SECONDS)

