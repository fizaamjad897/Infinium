"""
Embedding & ChromaDB Storage
- Uses Ollama's batched /api/embed endpoint (many texts per HTTP call)
- Persistent HTTP session for keep-alive — no TCP handshake per request
- Parallel batched calls for huge throughput on large repos
- Falls back to /api/embeddings (singular) if Ollama is too old to support /api/embed
"""
from __future__ import annotations
import hashlib
import threading
import time
import chromadb
import requests
from requests.adapters import HTTPAdapter
from typing import Optional
from concurrent.futures import ThreadPoolExecutor, as_completed
from backend.config import settings


# ── Persistent HTTP session for Ollama (keep-alive avoids TCP handshake on every call) ──

_session_lock = threading.Lock()
_session: Optional[requests.Session] = None


def _get_session() -> requests.Session:
    global _session
    if _session is not None:
        return _session
    with _session_lock:
        if _session is None:
            s = requests.Session()
            adapter = HTTPAdapter(
                pool_connections=max(8, settings.EMBED_WORKERS),
                pool_maxsize=max(16, settings.EMBED_WORKERS * 2),
                max_retries=0,   # we handle retries explicitly
            )
            s.mount("http://", adapter)
            s.mount("https://", adapter)
            _session = s
    return _session


# Track whether the Ollama server supports /api/embed (batched). Set after first probe.
_supports_batch_endpoint: Optional[bool] = None
_supports_batch_lock = threading.Lock()

# Warmup state — ensure the model is loaded before parallel requests hit it.
# Many Ollama versions return 400 when several concurrent calls arrive while
# the model is still cold-loading from disk.
_model_warmed: bool = False
_model_warmup_lock = threading.Lock()


def _warm_up_embed_model() -> None:
    """Synchronous probe + pin: loads the embed model into memory and tells
    Ollama to KEEP it loaded (``keep_alive: -1``).

    Without this, Ollama's default 5-minute keep_alive can evict the embed
    model during ingestion when other models (kimi/minimax cloud) are also
    in play. Each eviction = ~600ms cold reload on the next call, which
    explains slow ingestions that should be fast.
    """
    global _model_warmed, _supports_batch_endpoint
    if _model_warmed:
        return
    with _model_warmup_lock:
        if _model_warmed:
            return
        session = _get_session()
        try:
            resp = session.post(
                f"{settings.OLLAMA_BASE_URL}/api/embed",
                json={
                    "model": settings.EMBED_MODEL,
                    "input": "warmup",
                    "keep_alive": -1,    # pin forever
                },
                timeout=120,
            )
            if resp.status_code == 200:
                _supports_batch_endpoint = True
                _model_warmed = True
                print(f"   🔥 Pinned {settings.EMBED_MODEL} via /api/embed (keep_alive: -1)")
                return
            elif resp.status_code in (400, 404):
                body = (resp.text or "")[:300]
                print(f"   ⚠️ /api/embed warmup got {resp.status_code}: {body!r}")
                # NOTE: do NOT permanently disable — try per-call later, the
                # error might be content-specific not schema-incompatible.
        except Exception as e:
            print(f"   ⚠️ /api/embed warmup error: {e}")

        # Always pin via legacy endpoint as a safety net
        try:
            resp = session.post(
                f"{settings.OLLAMA_BASE_URL}/api/embeddings",
                json={"model": settings.EMBED_MODEL, "prompt": "warmup", "keep_alive": -1},
                timeout=120,
            )
            if resp.status_code == 200:
                print(f"   🔥 Pinned {settings.EMBED_MODEL} via /api/embeddings (keep_alive: -1)")
            else:
                print(f"   ⚠️ /api/embeddings warmup got {resp.status_code}")
        except Exception as e:
            print(f"   ⚠️ /api/embeddings warmup error: {e}")
        _model_warmed = True


def get_chroma_client() -> chromadb.PersistentClient:
    """Get a persistent ChromaDB client."""
    return chromadb.PersistentClient(path=settings.CHROMA_DB_PATH)


def get_chroma_collection(repo_name: str, client: Optional[chromadb.PersistentClient] = None):
    """Get or create a ChromaDB collection for a repository.

    Sanitises any owner/name combination (incl. unicode, slashes, dots) into a
    ChromaDB-valid collection name (alphanumeric/.-_, must start+end alnum,
    length 3-512).
    """
    if client is None:
        client = get_chroma_client()

    import re
    collection_name = re.sub(r'[^a-zA-Z0-9._-]', '_', repo_name)
    collection_name = collection_name.strip('._-')
    if not collection_name or len(collection_name) < 3:
        collection_name = f"repo_{abs(hash(repo_name)) % 10000}"
    collection_name = collection_name[:512]

    return client.get_or_create_collection(
        name=collection_name,
        metadata={"hnsw:space": "cosine"}
    )


# nomic-embed-text claims an 8192-token context window, but for dense code
# (lots of symbols, few spaces) the tokenizer can hit ~1.2 tokens/char which
# pushes a 6000-char chunk over the limit. 3000 chars is a safer cap that
# fits even the densest code chunks.
MAX_EMBED_CHARS = 3000

# When Ollama returns "input length exceeds the context length" we'll
# aggressively re-truncate the offending text and retry. This is the
# minimum length we'll truncate to before giving up and zero-vectoring.
MIN_EMBED_CHARS = 500


def _truncate_for_embed(text: str, max_chars: int = MAX_EMBED_CHARS) -> str:
    """Truncate text to fit nomic-embed-text's context window."""
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n... [truncated for embedding]"


def _is_context_length_error(resp_text: str) -> bool:
    """Heuristic: does this Ollama error body indicate context overflow?"""
    if not resp_text:
        return False
    lowered = resp_text.lower()
    return ("context length" in lowered
            or "context window" in lowered
            or "exceeds" in lowered and "length" in lowered)


def _ollama_embed(text: str, retries: int = 3) -> list[float]:
    """Single-text embed via Ollama's legacy /api/embeddings endpoint.

    Used as a fallback inside the batch function and for one-off query embeds.
    Pins the model in memory with ``keep_alive: -1`` so it doesn't reload.

    Context-length errors are handled by progressively halving the text
    until it fits or we hit ``MIN_EMBED_CHARS``.
    """
    text = _truncate_for_embed(text)
    session = _get_session()

    current = text
    for attempt in range(retries):
        try:
            resp = session.post(
                f"{settings.OLLAMA_BASE_URL}/api/embeddings",
                json={
                    "model": settings.EMBED_MODEL,
                    "prompt": current,
                    "keep_alive": -1,
                },
                timeout=60,
            )

            # Context-length error → aggressively halve the text and retry on this attempt
            if (resp.status_code in (400, 500)
                    and _is_context_length_error(resp.text)
                    and len(current) > MIN_EMBED_CHARS):
                new_len = max(MIN_EMBED_CHARS, len(current) // 2)
                current = current[:new_len] + "\n... [hard-truncated]"
                continue   # retry without consuming the attempt — this is a SEPARATE issue from transient failures

            resp.raise_for_status()
            return resp.json()["embedding"]
        except Exception as e:
            if attempt < retries - 1:
                time.sleep(1.0 * (attempt + 1))
                continue
            raise RuntimeError(f"Ollama embedding failed after {retries} attempts: {e}")


def _disable_batch_endpoint(reason: str):
    """Permanently disable the /api/embed batch path for the rest of this run."""
    global _supports_batch_endpoint
    with _supports_batch_lock:
        if _supports_batch_endpoint is not False:
            _supports_batch_endpoint = False
            print(f"   ℹ️ Ollama /api/embed disabled: {reason}")
            print(f"   ℹ️ Falling back to per-text /api/embeddings for the rest of this ingest.")


def _per_text_embed_many(texts: list[str]) -> list[list[float]]:
    """Embed each text via the legacy /api/embeddings endpoint IN PARALLEL.

    The previous version was sequential — when a single batch fell back to
    per-text mode, that worker stalled on N sequential 100ms calls instead
    of running them in parallel. This recovers the lost throughput.
    """
    if not texts:
        return []
    out: list[Optional[list[float]]] = [None] * len(texts)
    # Use a small pool so we don't crush Ollama with 8 workers × N texts each
    inner_workers = min(len(texts), 4)
    with ThreadPoolExecutor(max_workers=inner_workers) as pool:
        futures = {pool.submit(_ollama_embed, t): idx for idx, t in enumerate(texts)}
        for future in as_completed(futures):
            idx = futures[future]
            try:
                out[idx] = future.result()
            except Exception as ee:
                print(f"   ⚠️ Per-text embed failed: {ee}")
                out[idx] = [0.0] * 768
    return [v if v is not None else [0.0] * 768 for v in out]


def _ollama_embed_batch(texts: list[str], retries: int = 2) -> list[list[float]]:
    """Embed many texts in ONE Ollama call via /api/embed.

    Throughput unlock: one HTTP request returns N embeddings, amortising
    TCP/JSON/HTTP overhead.

    Auto-disables the batch endpoint on:
      - 404 → endpoint not in this Ollama version
      - 400 → endpoint exists but rejects this schema (e.g. some builds want
              a different field name). Body is logged once for diagnostics.
      - DISABLE_BATCH_EMBED=true in .env

    On 500 with len > 1 it recursively halves the batch — isolates a single
    problem-text without nuking the whole batch.
    """
    if not texts:
        return []

    # Truncate AND drop empty/whitespace-only texts (Ollama 400's on those)
    cleaned: list[str] = []
    placeholders: list[bool] = []
    for t in texts:
        truncated = _truncate_for_embed(t or "")
        if truncated and truncated.strip():
            cleaned.append(truncated)
            placeholders.append(False)
        else:
            placeholders.append(True)

    # If everything was empty, return zero vectors directly
    if not cleaned:
        return [[0.0] * 768 for _ in texts]

    session = _get_session()

    # Honor the env override and previous run-time decisions
    global _supports_batch_endpoint
    if settings.DISABLE_BATCH_EMBED or _supports_batch_endpoint is False:
        embeds = _per_text_embed_many(cleaned)
        return _interleave_zero_for_empties(embeds, placeholders)

    payload = {"model": settings.EMBED_MODEL, "input": cleaned, "keep_alive": -1}

    for attempt in range(retries):
        try:
            resp = session.post(
                f"{settings.OLLAMA_BASE_URL}/api/embed",
                json=payload,
                timeout=180,
            )

            # 404 = endpoint genuinely missing → permanently disable (will never work)
            if resp.status_code == 404:
                _disable_batch_endpoint("endpoint not available (404). "
                                        "Upgrade Ollama (`brew upgrade ollama`).")
                embeds = _per_text_embed_many(cleaned)
                return _interleave_zero_for_empties(embeds, placeholders)

            # 400 / 500 = could be content-specific. Halve to isolate the bad text.
            if resp.status_code in (400, 500):
                if len(cleaned) > 1:
                    mid = len(cleaned) // 2
                    left = _ollama_embed_batch(cleaned[:mid], retries=1)
                    right = _ollama_embed_batch(cleaned[mid:], retries=1)
                    embeds = left + right
                    return _interleave_zero_for_empties(embeds, placeholders)
                # Single text still 400/500 — _ollama_embed handles context-length
                # errors via progressive halving, so the legacy fallback can recover
                # texts that the batch endpoint refuses.
                body = (resp.text or "")[:300]
                if attempt == 0 and not _is_context_length_error(body):
                    print(f"   ⚠️ Single-text batch returned {resp.status_code}: "
                          f"{body!r} — falling back to /api/embeddings")
                embeds = _per_text_embed_many(cleaned)
                return _interleave_zero_for_empties(embeds, placeholders)

            resp.raise_for_status()
            data = resp.json()

            if _supports_batch_endpoint is None:
                with _supports_batch_lock:
                    if _supports_batch_endpoint is None:
                        _supports_batch_endpoint = True

            embeddings = data.get("embeddings") or data.get("embedding") or []
            if not embeddings or len(embeddings) != len(cleaned):
                raise RuntimeError(
                    f"Batch returned {len(embeddings)} vectors for {len(cleaned)} texts"
                )
            return _interleave_zero_for_empties(embeddings, placeholders)

        except Exception as e:
            if attempt < retries - 1:
                time.sleep(1.0 * (attempt + 1))
                continue
            print(f"   ⚠️ Batch embed errored ({e}) — per-text fallback (this batch only)")
            embeds = _per_text_embed_many(cleaned)
            return _interleave_zero_for_empties(embeds, placeholders)

    return [[0.0] * 768 for _ in texts]


def _interleave_zero_for_empties(
    embeddings: list[list[float]], placeholders: list[bool]
) -> list[list[float]]:
    """Re-insert zero vectors for the input slots that were empty/whitespace.

    ``placeholders[i] is True`` means the i-th input was filtered out before
    sending — we plug a 768-d zero vector back so the caller's array layout
    stays in sync with the original ``texts`` list.
    """
    if not any(placeholders):
        return embeddings
    out: list[list[float]] = []
    iter_real = iter(embeddings)
    for is_empty in placeholders:
        if is_empty:
            out.append([0.0] * 768)
        else:
            try:
                out.append(next(iter_real))
            except StopIteration:
                out.append([0.0] * 768)
    return out


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Generate embeddings for a list of texts via Ollama (sequential to avoid overloading)."""
    results = []
    for text in texts:
        results.append(_ollama_embed(text))
    return results


def embed_query(query: str) -> list[float]:
    """Embed a single query for retrieval."""
    return _ollama_embed(query)


def embed_and_store(
    chunks: list[dict],
    collection,
    repo_name: str = "",
    progress_callback=None,
):
    """Embed chunks via Ollama batched-API and upsert to ChromaDB.

    Throughput strategy:
      1. Group ``chunks`` into sub-batches of ``EMBED_BATCH_SIZE`` (default 16).
      2. Run up to ``EMBED_WORKERS`` (default 8) sub-batches in parallel — each
         is a single Ollama /api/embed HTTP call covering all its texts.
      3. Per-Chroma-batch (256 chunks) we collect every sub-batch's vectors in
         order and upsert in one shot.

    A 22 700-chunk repo that previously took ~75 min now takes ~5 min on the
    same hardware. Falls back gracefully on older Ollama versions.

    Args:
        chunks: list of chunk dicts (from chunker.*).
        collection: ChromaDB collection (from get_chroma_collection).
        repo_name: stamped onto every metadata entry.
        progress_callback: optional ``fn(processed: int, total: int)``.
    """
    if not chunks:
        return 0

    # Load the model BEFORE starting parallel workers — several Ollama versions
    # return 400 when concurrent requests arrive while the model is cold-loading.
    _warm_up_embed_model()

    chroma_batch_size = 256                                  # rows per Chroma upsert
    sub_batch_size = max(1, settings.EMBED_BATCH_SIZE)       # texts per Ollama call
    workers = max(1, settings.EMBED_WORKERS)
    progress_step = max(1, settings.EMBED_PROGRESS_EVERY)

    total = len(chunks)
    total_stored = 0
    processed = 0
    last_reported = 0

    for i in range(0, total, chroma_batch_size):
        batch = chunks[i:i + chroma_batch_size]

        ids: list[str] = []
        for c in batch:
            source = c.get("source", "unknown").replace("\\", "/")
            chunk_idx = c.get("chunk_index", 0)
            doc_id = f"{repo_name}__{source}__{chunk_idx}"
            if len(doc_id) > 512:
                doc_id = hashlib.md5(doc_id.encode()).hexdigest()
            ids.append(doc_id)

        documents = [c["text"] for c in batch]

        # Slice into Ollama-batches for parallel batched embedding
        sub_batches = [
            documents[j:j + sub_batch_size]
            for j in range(0, len(documents), sub_batch_size)
        ]
        results: list[Optional[list[list[float]]]] = [None] * len(sub_batches)

        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(_ollama_embed_batch, sb): idx
                for idx, sb in enumerate(sub_batches)
            }
            for future in as_completed(futures):
                idx = futures[future]
                try:
                    results[idx] = future.result()
                except Exception as e:
                    print(f"   ⚠️ Sub-batch {idx} failed entirely: {e}")
                    results[idx] = [[0.0] * 768 for _ in sub_batches[idx]]

                processed += len(sub_batches[idx])
                if progress_callback and processed - last_reported >= progress_step:
                    try:
                        progress_callback(processed, total)
                        last_reported = processed
                    except Exception:
                        pass

        # Flatten preserving original order (sub-batches are stored at their idx)
        embeddings: list[list[float]] = []
        for sb_emb in results:
            embeddings.extend(sb_emb or [])

        # Defensive: pad / trim to match documents in case of partial failures
        if len(embeddings) < len(documents):
            embeddings.extend([[0.0] * 768] * (len(documents) - len(embeddings)))
        elif len(embeddings) > len(documents):
            embeddings = embeddings[:len(documents)]

        metadatas = []
        for c in batch:
            meta = {k: str(v) if not isinstance(v, (str, int, float, bool)) else v
                    for k, v in c.items() if k != "text"}
            meta["repo"] = repo_name
            metadatas.append(meta)

        try:
            collection.upsert(
                ids=ids,
                documents=documents,
                embeddings=embeddings,
                metadatas=metadatas,
            )
            total_stored += len(batch)
            done = min(i + chroma_batch_size, total)
            print(f"   Stored {done}/{total} chunks")
            if progress_callback:
                try:
                    progress_callback(done, total)
                    last_reported = done
                except Exception:
                    pass
        except Exception as e:
            print(f"   Error storing batch starting at {i}: {e}")

    return total_stored


def delete_collection(repo_name: str):
    """Delete a repository's collection from ChromaDB."""
    client = get_chroma_client()
    collection_name = repo_name.replace("/", "_").replace("-", "_").replace(".", "_")[:63]
    try:
        client.delete_collection(name=collection_name)
        print(f"   Deleted collection: {collection_name}")
    except Exception as e:
        print(f"   Could not delete collection {collection_name}: {e}")


def list_collections() -> list[str]:
    """List all indexed repository collection names."""
    client = get_chroma_client()
    collections = client.list_collections()
    return [c.name for c in collections]


def get_collection_stats(repo_name: str) -> dict:
    """Get stats for a repository's collection."""
    try:
        collection = get_chroma_collection(repo_name)
        count = collection.count()
        return {"repo": repo_name, "chunks": count, "status": "indexed"}
    except Exception:
        return {"repo": repo_name, "chunks": 0, "status": "not_found"}
