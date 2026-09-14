import hashlib

import numpy as np
import pytest


class FakeEmbedder:
    """Deterministic embedder: a 64-dim vector derived from the text's word set.

    Texts sharing words get closer vectors, which is enough to test ranking logic.
    """

    model = "fake"
    dim = 64

    def embed(self, texts):
        out = []
        for t in texts:
            v = np.zeros(self.dim, dtype=np.float32)
            for w in t.lower().split():
                h = int(hashlib.md5(w.encode()).hexdigest(), 16)
                v[h % self.dim] += 1.0
            n = np.linalg.norm(v)
            out.append((v / n if n else v).tolist())
        return out


@pytest.fixture
def fake_embedder():
    return FakeEmbedder()
