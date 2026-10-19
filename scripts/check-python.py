from pathlib import Path
import ast
root = Path(__file__).resolve().parents[1] / "AI-Agent"
files = sorted((root / "backend").rglob("*.py")) + [root / name for name in ["run.py", "run_dev.py"]]
for file in files:
    ast.parse(file.read_text(), filename=str(file))
print(f"Python syntax: {len(files)} application files parsed without imports")
