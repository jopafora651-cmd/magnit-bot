"""Optional persistence of the SQLite database to a Hugging Face dataset repo.

On Hugging Face Spaces the container filesystem is ephemeral: every rebuild
wipes the local database. This module snapshots the DB to a private dataset
repo (created automatically) and restores it on startup.

Enabled only when the HF_TOKEN environment variable is set (e.g. in the
Space's secrets); locally the bot keeps working without any HF interaction.
"""

import hashlib
import logging
import os
import shutil
import sqlite3
import tempfile
from typing import Optional

from .config import DATABASE_PATH

logger = logging.getLogger(__name__)

# Seconds between DB backup pushes
PUSH_INTERVAL = 600


def _token() -> Optional[str]:
    return os.getenv("HF_TOKEN")


def _repo_id() -> Optional[str]:
    """Dataset repo id: HF_DB_REPO override or <owner>/magnit-bot-data."""
    repo = os.getenv("HF_DB_REPO")
    if repo:
        return repo
    author = os.getenv("SPACE_AUTHOR_NAME")
    if not author and os.getenv("SPACE_ID", "").strip():
        author = os.getenv("SPACE_ID").split("/")[0].strip()
    if author:
        return f"{author}/magnit-bot-data"
    return None


def is_enabled() -> bool:
    return bool(_token() and _repo_id())


def db_hash() -> Optional[str]:
    """Hash of the current DB file, used to skip unchanged uploads."""
    try:
        with open(DATABASE_PATH, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    except OSError:
        return None


def pull_db() -> bool:
    """Download the latest DB snapshot from the HF dataset repo.

    Returns True when a backup was restored.
    """
    token = _token()
    repo = _repo_id()
    if not token or not repo:
        return False
    try:
        from huggingface_hub import hf_hub_download

        path = hf_hub_download(
            repo_id=repo,
            filename="magnit_bot.db",
            repo_type="dataset",
            token=token,
        )
        os.makedirs(os.path.dirname(os.path.abspath(DATABASE_PATH)), exist_ok=True)
        shutil.copyfile(path, DATABASE_PATH)
        logger.info("Database restored from HF dataset %s", repo)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.info("No database backup restored (%s)", exc)
        return False


def _snapshot_db(dest_path: str) -> bool:
    """Consistent copy of the live DB using the sqlite3 backup API."""
    if not os.path.exists(DATABASE_PATH):
        return False
    try:
        src = sqlite3.connect(DATABASE_PATH)
        dst = sqlite3.connect(dest_path)
        with dst:
            src.backup(dst)
        src.close()
        dst.close()
        return True
    except sqlite3.Error:
        logger.warning("Failed to snapshot database")
        return False


def push_db() -> bool:
    """Upload a consistent DB snapshot to the HF dataset repo."""
    token = _token()
    repo = _repo_id()
    if not token or not repo:
        return False
    if not os.path.exists(DATABASE_PATH):
        return False

    try:
        from huggingface_hub import HfApi

        api = HfApi(token=token)
        api.create_repo(repo_id=repo, repo_type="dataset", private=True, exist_ok=True)

        with tempfile.TemporaryDirectory() as tmp:
            snapshot = os.path.join(tmp, "magnit_bot.db")
            if not _snapshot_db(snapshot):
                return False
            api.upload_file(
                path_or_fileobj=snapshot,
                path_in_repo="magnit_bot.db",
                repo_id=repo,
                repo_type="dataset",
            )
        logger.info("Database backup pushed to %s", repo)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("Database push failed: %s", exc)
        return False