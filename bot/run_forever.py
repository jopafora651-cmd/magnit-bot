"""Supervisor loop: keeps the bot running and restarts it after crashes.

Started automatically at logon by the Task Scheduler task "MagnitBot"
(via pythonw.exe - no console window). Logs to data/supervisor.log.
"""

import logging
import os
import socket
import subprocess
import sys
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
LOG_FILE = BASE_DIR / "data" / "supervisor.log"
CRASH_LOG = BASE_DIR / "data" / "bot_stderr.log"
LOCK_PORT = int(os.getenv("SUPERVISOR_LOCK_PORT", "47577"))

# Keep a module-level reference so the socket stays open for our lifetime
_lock_socket: "socket.socket | None" = None


def setup_logging() -> None:
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
        handlers=[RotatingFileHandler(LOG_FILE, maxBytes=200_000, backupCount=2, encoding="utf-8")],
    )


def acquire_single_instance_lock() -> bool:
    """Return False when another supervisor instance is already running.

    The OS closes the bound socket when the process dies, so the lock can
    never become stale.
    """
    global _lock_socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind(("127.0.0.1", LOCK_PORT))
        s.listen(1)
        _lock_socket = s
        return True
    except OSError:
        return False


def main() -> None:
    setup_logging()
    if not acquire_single_instance_lock():
        logging.warning("Another supervisor is already running; exiting.")
        return

    logging.info("Supervisor started (python %s)", sys.version.split()[0])
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

    while True:
        try:
            # Capture the bot's stdout/stderr so crash tracebacks are not
            # lost (pythonw has no console, they would otherwise vanish).
            CRASH_LOG.parent.mkdir(parents=True, exist_ok=True)
            with open(CRASH_LOG, "wb") as errf:
                errf.write(f"--- bot start {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n".encode())
                errf.flush()
                rc = subprocess.call(
                    [sys.executable, "-m", "bot.main"],
                    cwd=str(BASE_DIR),
                    creationflags=creationflags,
                    stdout=errf,
                    stderr=errf,
                )
            logging.warning("Bot exited with code %s; restarting in 10 s", rc)
        except Exception:  # noqa: BLE001
            logging.exception("Failed to launch the bot")
        time.sleep(10)


if __name__ == "__main__":
    main()
