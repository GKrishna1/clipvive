import time
from app.tasks import cleanup_local_storage

# Run cleanup every 24 hours. For container/pod simplicity we loop.
SLEEP_SECONDS = 24 * 3600

if __name__ == "__main__":
    while True:
        res = cleanup_local_storage()
        print("cleanup result:", res)
        # sleep until next run
        time.sleep(SLEEP_SECONDS)
