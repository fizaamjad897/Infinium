import requests
import json

url = "http://localhost:11434/api/pull"
data = {"name": "llama3.2:1b"}

print("🚀 Starting Llama 3.2 (1B) download via API...")
print("Please wait, this will take a few minutes (4.7GB)...")

try:
    with requests.post(url, json=data, stream=True) as response:
        response.raise_for_status()
        for line in response.iter_lines():
            if line:
                status = json.loads(line)
                if 'completed' in status and 'total' in status:
                    percent = (status['completed'] / status['total']) * 100
                    print(f"📥 Downloading: {percent:.2f}%", end="\r")
                elif 'status' in status:
                    print(f"🔄 Status: {status['status']}")
    print("\n✅ Download Finished!")
except Exception as e:
    print(f"\n❌ Error: {e}")
    print("Make sure Ollama is open in your system tray.")
