"""
Embedding & ChromaDB Storage
- Uses Ollama API for embeddings (lightweight, no PyTorch needed)
- Stores and retrieves from ChromaDB
- Batch processing for efficiency
"""
import chromadb
import requests
from typing import Optional
from backend.config import settings


def get_chroma_client() -> chromadb.PersistentClient:
    """Get a persistent ChromaDB client."""
    return chromadb.PersistentClient(path=settings.CHROMA_DB_PATH)


def get_chroma_collection(repo_name: str, client: Optional[chromadb.PersistentClient] = None):
    """Get or create a ChromaDB collection for a repository."""
    if client is None:
        client = get_chroma_client()
    
    collection_name = repo_name.replace("/", "_").replace("-", "_").replace(".", "_")
    collection_name = collection_name[:63]
    if len(collection_name) < 3:
        collection_name = collection_name + "_repo"
    
    return client.get_or_create_collection(
        name=collection_name,
        metadata={"hnsw:space": "cosine"}
    )


def _ollama_embed(text: str) -> list[float]:
    """Get embedding from Ollama API using whatever model is available."""
    try:
        resp = requests.post(
            f"{settings.OLLAMA_BASE_URL}/api/embeddings",
            json={"model": settings.LLM_MODEL, "prompt": text},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()["embedding"]
    except Exception as e:
        raise RuntimeError(f"Ollama embedding failed: {e}. Make sure Ollama is running.")


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Generate embeddings for a list of texts via Ollama."""
    return [_ollama_embed(t) for t in texts]


def embed_query(query: str) -> list[float]:
    """Embed a single query for retrieval."""
    return _ollama_embed(query)


def embed_and_store(chunks: list[dict], collection, repo_name: str = ""):
    """Batch upsert chunks into ChromaDB with Ollama embeddings."""
    if not chunks:
        return 0
    
    batch_size = 20  # Smaller batches since we embed one-by-one via API
    total_stored = 0
    
    for i in range(0, len(chunks), batch_size):
        batch = chunks[i:i + batch_size]
        
        ids = []
        for c in batch:
            source = c.get("source", "unknown").replace("\\", "/")
            chunk_idx = c.get("chunk_index", 0)
            doc_id = f"{repo_name}__{source}__{chunk_idx}"
            if len(doc_id) > 512:
                import hashlib
                doc_id = hashlib.md5(doc_id.encode()).hexdigest()
            ids.append(doc_id)
        
        documents = [c["text"] for c in batch]
        
        # Generate embeddings via Ollama
        embeddings = [_ollama_embed(doc) for doc in documents]
        
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
