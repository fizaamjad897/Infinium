import chromadb
from sentence_transformers import SentenceTransformer
import requests
import json

class PrototypeQueryEngine:
    def __init__(self, db_dir: str = "./chroma_db", collection_name: str = "main_repo"):
        self.client = chromadb.PersistentClient(path=db_dir)
        self.collection = self.client.get_collection(name=collection_name)
        self.model = SentenceTransformer("all-MiniLM-L6-v2")
        self.ollama_url = "http://localhost:11434/api/generate"
        self.llm_model = "llama3.2:1b" # Much faster model

    def ask(self, query: str):
        print(f"🔍 Searching local knowledge for: {query}")
        
        # 1. Embed query
        query_embedding = self.model.encode(query).tolist()
        
        # 2. Retrieve top 3 snippets (Reduced from 5 for speed)
        results = self.collection.query(
            query_embeddings=[query_embedding],
            n_results=3
        )
        
        context = ""
        sources = []
        for i in range(len(results['documents'][0])):
            doc = results['documents'][0][i]
            metadata = results['metadatas'][0][i]
            context += f"\n---\nSource: {metadata['source']}\nContent: {doc}\n"
            sources.append(metadata['source'])
        
        print(f"📊 Context size: {len(context)} characters")

        # 3. Generate Answer with Ollama
        prompt = f"""
Using the codebase snippets below, answer the question briefly. 
If unsure, say you don't know.

Context:
{context}

Question: {query}

Brief Answer:"""

        print("🧠 Thinking with Llama 3 (this can take a minute)...")
        try:
            response = requests.post(
                self.ollama_url,
                json={
                    "model": self.llm_model,
                    "prompt": prompt,
                    "stream": False
                },
                timeout=300 # Increased timeout to 5 minutes
            )
            response.raise_for_status()
            answer = response.json().get("response", "No response from AI.")
        except Exception as e:
            answer = f"Error connecting to AI: {e}\n(Make sure Ollama is running and Llama3 is pulled)"

        return {
            "answer": answer,
            "sources": list(set(sources)),
            "retrieved_docs": results['documents'][0]
        }

if __name__ == "__main__":
    engine = PrototypeQueryEngine()
    res = engine.ask("What is the heart disease prediction model?")
    print(f"\n📢 ANSWER:\n{res['answer']}")
