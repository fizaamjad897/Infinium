from pathlib import Path
import re
root = Path(__file__).resolve().parents[1]
files = [root / x for x in ["README.md", "CONTRIBUTING.md", "SECURITY.md", "SUPPORT.md"]] + list((root / "docs").glob("*.md"))
missing = []
for file in files:
    for target in re.findall(r"(?<!!)\[[^\]]*\]\(([^)]+)\)", file.read_text()):
        if target.startswith(("https://", "http://", "#", "mailto:")):
            continue
        target = target.split("#")[0]
        if target and not (file.parent / target).exists():
            missing.append(f"{file.relative_to(root)} -> {target}")
if missing:
    raise SystemExit("Missing documentation links:\n" + "\n".join(missing))
print(f"Documentation links: {len(files)} files checked")
