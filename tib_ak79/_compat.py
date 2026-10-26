"""Stand-alone replacements for the two helpers the private codebase provided.  Stdlib only."""
from __future__ import annotations

import os
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

SSH_SIG_PREFIX = "ssh:"  # prefix of an SSH-signature string in a custody ledger entry


@contextmanager
def file_lock(path: Path, timeout: float = 30.0) -> Iterator[None]:
    """Single-writer advisory lock on `path` (created if missing).  Raises TimeoutError when not acquired in `timeout` seconds."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(path), os.O_RDWR | os.O_CREAT, 0o600)
    deadline = time.monotonic() + timeout
    try:
        while True:
            try:
                if sys.platform == "win32":
                    import msvcrt
                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() > deadline:
                    raise TimeoutError("lock_timeout")
                time.sleep(0.005)
        try:
            yield
        finally:
            if sys.platform == "win32":
                import msvcrt
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)
