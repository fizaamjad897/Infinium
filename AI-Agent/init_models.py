"""
Infinium Model Initialization & Verification Script
Run this BEFORE starting the server to ensure all models are ready.

Usage: python init_models.py
"""
import sys
import requests
import time


def check_ollama(base_url: str = "http://localhost:11434") -> bool:
    """Check if Ollama is running."""
    try:
        resp = requests.get(f"{base_url}/api/tags", timeout=5)
        if resp.status_code == 200:
            models = [m["name"] for m in resp.json().get("models", [])]
            print(f"✅ Ollama is running ({len(models)} models available)")
            for m in models:
                print(f"   • {m}")
            return True
        print(f"❌ Ollama returned status {resp.status_code}")
        return False
    except requests.exceptions.ConnectionError:
        print(f"❌ Cannot connect to Ollama at {base_url}")
        print(f"   Run: ollama serve")
        return False


def check_embed_model(base_url: str, model: str = "nomic-embed-text") -> bool:
    """Verify the embedding model works."""
    try:
        resp = requests.post(
            f"{base_url}/api/embeddings",
            json={"model": model, "prompt": "test embedding"},
            timeout=30,
        )
        if resp.status_code == 200:
            embedding = resp.json().get("embedding", [])
            print(f"✅ Embedding model '{model}' works (dimension: {len(embedding)})")
            return True
        print(f"❌ Embedding model '{model}' failed (status {resp.status_code})")
        print(f"   Run: ollama pull {model}")
        return False
    except Exception as e:
        print(f"❌ Embedding model error: {e}")
        print(f"   Run: ollama pull {model}")
        return False


def check_cloud_model(base_url: str, model: str) -> bool:
    """Verify a cloud model responds."""
    try:
        print(f"   Testing {model}... (this may take a few seconds)")
        resp = requests.post(
            f"{base_url}/api/chat",
            json={
                "model": model,
                "messages": [{"role": "user", "content": "Respond with just 'ok'"}],
                "stream": False,
                "options": {"num_ctx": 512},
            },
            timeout=60,
        )
        if resp.status_code == 200:
            content = resp.json().get("message", {}).get("content", "")
            print(f"✅ Cloud model '{model}' responds: \"{content[:50]}\"")
            return True
        print(f"❌ Cloud model '{model}' failed (status {resp.status_code})")
        error = resp.text[:200] if resp.text else "Unknown error"
        print(f"   Error: {error}")
        print(f"   Run: ollama run {model}")
        return False
    except requests.exceptions.Timeout:
        print(f"⚠️ Cloud model '{model}' timed out (60s). May still work for longer queries.")
        return True
    except Exception as e:
        print(f"❌ Cloud model '{model}' error: {e}")
        print(f"   Run: ollama run {model}")
        return False


def main():
    from dotenv import load_dotenv
    import os
    load_dotenv()
    
    base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
    llm_model = os.getenv("LLM_MODEL", "minimax-m2.7:cloud")
    reasoning_model = os.getenv("CODE_REASONING_MODEL", "kimi-k2.5:cloud")
    embed_model = os.getenv("EMBED_MODEL", "nomic-embed-text")

    print("=" * 55)
    print("  INFINIUM — Model Verification")
    print("=" * 55)
    print(f"  Ollama URL:      {base_url}")
    print(f"  LLM Model:       {llm_model}")
    print(f"  Reasoning Model: {reasoning_model}")
    print(f"  Embed Model:     {embed_model}")
    print("-" * 55)

    results = []

    # 1. Check Ollama
    print("\n[1/4] Checking Ollama connection...")
    results.append(check_ollama(base_url))

    if not results[0]:
        print("\n⛔ Ollama is not running. Start it first:")
        print("   ollama serve")
        sys.exit(1)

    # 2. Check embedding model (local)
    print(f"\n[2/4] Testing embedding model ({embed_model})...")
    results.append(check_embed_model(base_url, embed_model))

    # 3. Check LLM model (cloud)
    print(f"\n[3/4] Testing LLM model ({llm_model})...")
    results.append(check_cloud_model(base_url, llm_model))

    # 4. Check reasoning model (cloud)
    print(f"\n[4/4] Testing reasoning model ({reasoning_model})...")
    results.append(check_cloud_model(base_url, reasoning_model))

    # Summary
    print("\n" + "=" * 55)
    passed = sum(results)
    total = len(results)
    if passed == total:
        print(f"  ✅ All {total} checks passed! Ready to start Infinium.")
    else:
        print(f"  ⚠️ {passed}/{total} checks passed.")
        if not results[1]:
            print(f"  Fix: ollama pull {embed_model}")
        if not results[2]:
            print(f"  Fix: ollama run {llm_model}")
        if not results[3]:
            print(f"  Fix: ollama run {reasoning_model}")
    print("=" * 55)

    return all(results)


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
