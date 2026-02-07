import os
import sys
from ingestor import PrototypeIngestor
from query_engine import PrototypeQueryEngine

def run_test():
    print("🚀 Starting Phase 0 Prototype Test...")
    
    # 1. Ingest
    target_projects = [
        {'path': "d:/Infinium/Raveeha - Domain(Health)", 'repo_name': 'Health-Backend'},
        {'path': "d:/Infinium/HumanEval-XL", 'repo_name': 'HumanEval-XL'},
        {'path': "d:/Infinium/polyglot", 'repo_name': 'Polyglot-Demo'},
        {'path': "d:/Infinium/nx-monorepo-full-stack-app", 'repo_name': 'Nx-Monorepo'}
    ]
    ingestor = PrototypeIngestor()
    
    print("--- Step 1: Multi-Repo Ingestion ---")
    ingestor.ingest_folders(target_projects)
    
    # 2. Query
    print("\n--- Step 2: Testing Retrieval ---")
    engine = PrototypeQueryEngine()
    
    test_queries = [
        "What programming languages are used in the polyglot project?",
        "How is the temperature converter implemented in different languages?",
        "Explain the Nx monorepo structure and its apps/libs folders.",
        "What is the heart disease prediction model?",
        "What are the main goals of the HumanEval-XL project?"
    ]
    
    for q in test_queries:
        print(f"\n❓ Testing Retrieval for: {q}")
        # Test just the retriever part thru the engine but without calling LLM
        query_embedding = engine.model.encode(q).tolist()
        results = engine.collection.query(query_embeddings=[query_embedding], n_results=3)
        
        docs = results['documents'][0]
        metadatas = results['metadatas'][0]
        
        print(f"✅ Found {len(docs)} relevant snippets.")
        for i, doc in enumerate(docs):
            print(f"  [{i+1}] Source: {metadatas[i]['source']}")
            print(f"      Snippet: {doc[:150].strip().replace('\n', ' ')}...")

    print("\n--- Step 3: Testing Generation (Requires Ollama) ---")
    for q in test_queries:
        print(f"\n❓ Asking AI: {q}")
        try:
            response = engine.ask(q)
            print(f"💡 AI Answer: {response['answer']}")
        except Exception as e:
            print(f"⚠️ Generation Failed: {e}")
            print("Note: This is expected if Ollama is not installed or Llama3 is not pulled.")

if __name__ == "__main__":
    run_test()
