"""On-disk cache of the normalized embedding matrix, so each CLI process skips the SQLite rebuild.

One file per model next to the index (``index.sqlite.<model>.vectors``)::

    b"GTNHVEC1" | u64 LE header length | JSON header | zero padding to 64 bytes | float32 LE rows

The header records the model, the ``embeddings_version`` token the matrix was built from, the
shape and the chunk ids in row order. The matrix is exactly what ``index.load_matrix`` returns
and is memory-mapped on read, so scores are bit-identical to the uncached path. A file whose
token no longer matches the index is simply rebuilt; writes are atomic (temp file + rename).
"""
from __future__ import annotations

import json
import os
import re
import struct
import tempfile
from pathlib import Path

import numpy as np

_MAGIC = b"GTNHVEC1"
_ALIGN = 64
_DTYPE = np.dtype("<f4")


def cache_path(index_path: Path, model: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", model)
    return Path(f"{index_path}.{safe}.vectors")


def read(path: Path, model: str, token: bytes) -> tuple[list[str], np.ndarray] | None:
    """Return (ids, matrix) if ``path`` holds the matrix for this model and token, else None.

    Anything missing, stale, truncated or malformed is a miss, never an error.
    """
    try:
        with open(path, "rb") as fh:
            size = os.fstat(fh.fileno()).st_size
            if fh.read(len(_MAGIC)) != _MAGIC:
                return None
            (hlen,) = struct.unpack("<Q", fh.read(8))
            if hlen > size:
                return None
            header = json.loads(fh.read(hlen))
        if header["model"] != model or header["token"] != token.hex():
            return None
        rows, dim, offset, ids = int(header["rows"]), int(header["dim"]), int(header["offset"]), header["ids"]
        if not isinstance(ids, list) or len(ids) != rows or rows == 0 or dim <= 0 or offset < 16 + hlen:
            return None
        if size != offset + rows * dim * _DTYPE.itemsize:
            return None
        return ids, np.memmap(path, dtype=_DTYPE, mode="r", offset=offset, shape=(rows, dim))
    except (OSError, ValueError, TypeError, KeyError, OverflowError, struct.error):
        return None


def write(path: Path, model: str, token: bytes, ids: list[str], mat: np.ndarray) -> None:
    """Atomically write the cache file. Raises OSError on failure."""
    mat = np.ascontiguousarray(mat, dtype=_DTYPE)
    header = {"model": model, "token": token.hex(), "rows": mat.shape[0], "dim": mat.shape[1], "ids": ids, "offset": 0}
    # The offset is part of the header, so its own length feeds back into it; iterate to a fixpoint.
    while True:
        blob = json.dumps(header, separators=(",", ":")).encode()
        prefix = len(_MAGIC) + 8 + len(blob)
        offset = -(-prefix // _ALIGN) * _ALIGN
        if header["offset"] == offset:
            break
        header["offset"] = offset
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        umask = os.umask(0)
        os.umask(umask)
        if hasattr(os, "fchmod"):  # mkstemp creates 0600; match what open() would have made
            os.fchmod(fd, 0o666 & ~umask)
        with os.fdopen(fd, "wb") as fh:
            fh.write(_MAGIC + struct.pack("<Q", len(blob)) + blob + b"\0" * (offset - prefix))
            mat.tofile(fh)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
