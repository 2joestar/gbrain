import hashlib
from typing import List

_st_model = None
_ST_AVAILABLE = False

def _try_load_st():
    global _st_model, _ST_AVAILABLE
    if _ST_AVAILABLE:
        return True
    try:
        from sentence_transformers import SentenceTransformer
        _st_model = SentenceTransformer("all-MiniLM-L6-v2")
        _ST_AVAILABLE = True
    except Exception:
        pass
    return _ST_AVAILABLE

def embed(text: str) -> List[float]:
    """384-dim embedding. Uses sentence-transformers if available, else deterministic fallback."""
    if _try_load_st() and _st_model:
        try:
            vec = _st_model.encode([text[:512]])
            return vec[0].tolist()
        except Exception:
            pass

    # Deterministic fallback: char frequency spread across 384 dims
    text_bytes = text.encode("utf-8", errors="replace")[:512]
    h = hashlib.sha256(text_bytes).digest()
    vec = []
    for i in range(384):
        b1 = h[i % 32]
        b2 = h[(i * 7 + 13) % 32]
        vec.append(((b1 * 256 + b2) / 65535.0) - 0.5)
    return vec

def is_semantic() -> bool:
    """True if real sentence-transformer embeddings are in use."""
    return _try_load_st()
