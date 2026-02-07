import requests

try:
    response = requests.get("http://localhost:11434/api/tags")
    print(f"Status: {response.status_code}")
    print(f"Models: {response.json()}")
except Exception as e:
    print(f"Error: {e}")
