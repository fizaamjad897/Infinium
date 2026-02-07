import os
import chromadb
from sentence_transformers import SentenceTransformer
import glob
from typing import List
import hashlib

class PrototypeIngestor:
    def __init__(self, db_dir: str = "./chroma_db"):
        self.db_dir = db_dir
        self.client = chromadb.PersistentClient(path=db_dir)
        self.model = SentenceTransformer("all-MiniLM-L6-v2")
        self.collection_name = "main_repo"

    def get_file_hash(self, file_path):
        """Generate a hash for the file content."""
        with open(file_path, 'rb') as f:
            return hashlib.md5(f.read()).hexdigest()

    def ingest_folders(self, folder_configs: List[dict]):
        print(f"🚀 Starting multi-repo ingestion...")
        collection = self.client.get_or_create_collection(name=self.collection_name)

        extensions = ['*.py', '*.js', '*.ts', '*.md', '*.txt', '*.java', '*.cpp', '*.html', '*.css']
        
        total_chunks = 0
        skipped_files = 0
        
        for config in folder_configs:
            path = config['path']
            repo_name = config['repo_name']
            
            print(f"🔍 Scanning {repo_name} at: {path}")
            all_files = []
            for ext in extensions:
                all_files.extend(glob.glob(os.path.join(path, "**", ext), recursive=True))

            for file_path in all_files:
                try:
                    file_hash = self.get_file_hash(file_path)
                    
                    # Check if any chunk from this file already exists with this hash
                    # NOTE: We use the source and hash in metadata for checking
                    existing = collection.get(
                        where={"$and": [
                            {"source": file_path},
                            {"hash": file_hash}
                        ]},
                        limit=1
                    )
                    
                    if existing['ids']:
                        skipped_files += 1
                        continue

                    # If hash changed, delete old entries for this file
                    collection.delete(where={"source": file_path})

                    with open(file_path, 'r', encoding='utf-8') as f:
                        content = f.read()
                    
                    ext = os.path.splitext(file_path)[1].lower()
                    lang = {'.py': 'python', '.js': 'javascript', '.ts': 'typescript'}.get(ext, 'text')

                    chunk_size = 1500
                    chunks = [content[i:i+chunk_size] for i in range(0, len(content), chunk_size)]
                    
                    for i, chunk in enumerate(chunks):
                        embedding = self.model.encode(chunk).tolist()
                        collection.add(
                            embeddings=[embedding],
                            documents=[chunk],
                            metadatas=[{
                                "source": file_path,
                                "repo": repo_name,
                                "language": lang,
                                "hash": file_hash,
                                "chunk_index": i
                            }],
                            ids=[f"{repo_name}_{os.path.basename(file_path)}_{i}"]
                        )
                        total_chunks += 1
                except Exception as e:
                    print(f"    ⚠️ Error processing {file_path}: {e}")

        print(f"✅ Ingestion complete! Added {total_chunks} chunks, skipped {skipped_files} unchanged files.")

if __name__ == "__main__":
    ingestor = PrototypeIngestor()
    # Example: Adding multiple projects from different languages
    projects = [
        {'path': "d:/Infinium/Raveeha - Domain(Health)", 'repo_name': 'Health-Backend'},
        # You can add more here:
        # {'path': "d:/Projects/MyReactApp", 'repo_name': 'Frontend-Portal'}
    ]
    ingestor.ingest_folders(projects)
