"""
Embedding & ChromaDB Storage
- Uses Ollama API for embeddings (lightweight, no PyTorch needed)
- Stores and retrieves from ChromaDB
- Parallel batch processing for fast ingestion
"""
from __future__ import annotations
import hashlib
import chromadb
import requests
from typing import Optional
from concurrent.futures import ThreadPoolExecutor, as_completed
from backend.config import settings


def get_chroma_client() -> chromadb.PersistentClient:
    """Get a persistent ChromaDB client."""
    return chromadb.PersistentClient(path=settings.CHROMA_DB_PATH)


def get_chroma_collection(repo_name: str, client: Optional[chromadb.PersistentClient] = None):
    """Get or create a ChromaDB collection for a repository."""
    if client is None:
        client = get_chroma_client()

    # Clean the repo name to be ChromaDB compliant
    import re
    collection_name = re.sub(r'[^a-zA-Z0-9._-]', '_', repo_name)
    
    # Ensure it starts and ends with alphanumeric (not underscore or hyphen)
    collection_name = collection_name.strip('._-')
    
    # If empty after cleaning, use a hash-based name
    if not collection_name or len(collection_name) < 3:
        collection_name = f"repo_{abs(hash(repo_name)) % 10000}"
    
    # Truncate to ChromaDB's limit (512 chars)
    collection_name = collection_name[:512]
    
    print(f"   📁 Original repo name: {repo_name}")
    print(f"   📁 ChromaDB collection: {collection_name}")

    return client.get_or_create_collection(
        name=collection_name,
        metadata={"hnsw:space": "cosine"}
    )


def _ollama_embed(text: str, retries: int = 3) -> list[float]:
    """Get embedding from Ollama API with retry on transient errors."""
    import time
    for attempt in range(retries):
        try:
            resp = requests.post(
                f"{settings.OLLAMA_BASE_URL}/api/embeddings",
                json={"model": settings.EMBED_MODEL, "prompt": text},
                timeout=30,
            )
            resp.raise_for_status()
            return resp.json()["embedding"]
        except Exception as e:
            if attempt < retries - 1:
                time.sleep(0.5 * (attempt + 1))  # 0.5s, 1s backoff
                continue
            raise RuntimeError(f"Ollama embedding failed after {retries} attempts: {e}")



def embed_texts(texts: list[str]) -> list[list[float]]:
    """Generate embeddings for a list of texts via Ollama (parallel)."""
    results = [None] * len(texts)
    with ThreadPoolExecutor(max_workers=8) as executor:
        future_to_idx = {executor.submit(_ollama_embed, t): i for i, t in enumerate(texts)}
        for future in as_completed(future_to_idx):
            idx = future_to_idx[future]
            results[idx] = future.result()
    return results


def embed_query(query: str) -> list[float]:
    """Embed a single query for retrieval."""
    return _ollama_embed(query)


def embed_and_store(chunks: list[dict], collection, repo_name: str = ""):
    """Batch upsert chunks into ChromaDB with Ollama embeddings.

    Embeds chunks in parallel (8 concurrent Ollama calls) for 5-8x faster ingestion.
    """
    if not chunks:
        return 0

    batch_size = 50  # Larger batches now that embedding is parallel
    total_stored = 0

    for i in range(0, len(chunks), batch_size):
        batch = chunks[i:i + batch_size]

        # Build IDs
        ids = []
        for c in batch:
            source = c.get("source", "unknown").replace("\\", "/")
            chunk_idx = c.get("chunk_index", 0)
            doc_id = f"{repo_name}__{source}__{chunk_idx}"
            if len(doc_id) > 512:
                doc_id = hashlib.md5(doc_id.encode()).hexdigest()
            ids.append(doc_id)

        documents = [c["text"] for c in batch]

        # Embed all chunks in parallel
        embeddings = [None] * len(documents)
        with ThreadPoolExecutor(max_workers=3) as executor:
            future_to_idx = {
                executor.submit(_ollama_embed, doc): idx
                for idx, doc in enumerate(documents)
            }
            for future in as_completed(future_to_idx):
                idx = future_to_idx[future]
                try:
                    embeddings[idx] = future.result()
                except Exception as e:
                    print(f"   ⚠️ Embedding failed for chunk {idx}: {e}")
                    embeddings[idx] = [0.0] * 768  # fallback zero vector

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
            print(f"   Stored {min(i + batch_size, len(chunks))}/{len(chunks)} chunks")
        except Exception as e:
            print(f"   Error storing batch {i}: {e}")

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
