import os
from pathlib import Path

def analyze_folder(folder_path):
    print(f"Analyzing folder: {folder_path}")
    
    # Get all files with extensions
    files = []
    for root, dirs, filenames in os.walk(folder_path):
        for filename in filenames:
            full_path = os.path.join(root, filename)
            rel_path = os.path.relpath(full_path, folder_path)
            files.append({
                'path': rel_path,
                'extension': os.path.splitext(filename)[1],
                'size': os.path.getsize(full_path)
            })
    
    print(f"\nFound {len(files)} files:")
    print("-" * 50)
    for file in sorted(files, key=lambda x: x['path']):
        print(f"{file['path']} ({file['extension']}) - {file['size']} bytes")
    
    return files

# Run it
if __name__ == "__main__":
    folder_path = "D:/Infinium/Raveeha - Domain(Health)/AI-CardioCare (TestRepo)" 
    files = analyze_folder(folder_path)