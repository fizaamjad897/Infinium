"""Load only the pure chunker: no API/settings/store/model imports."""
import importlib.util
from pathlib import Path
import unittest

file = Path(__file__).resolve().parents[1] / "AI-Agent/backend/ingestion/code_chunker.py"
spec = importlib.util.spec_from_file_location("offline_chunker", file)
chunker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(chunker)

class ChunkingTests(unittest.TestCase):
    def test_python_decorator_and_complete_function_stay_together(self):
        source = "import os\n\n@decorator\ndef first(value):\n    return value + 1\n\ndef second():\n    return 2\n"
        chunks = chunker.function_aware_chunks(source, ".py")
        first = next(c for c in chunks if c["name"] == "first")
        self.assertIn("@decorator", first["text"])
        self.assertIn("return value + 1", first["text"])
        self.assertNotIn("def second", first["text"])
        self.assertTrue(any(c["kind"] == "module" and "import os" in c["text"] for c in chunks))

    def test_class_methods_are_addressable_without_splitting_class(self):
        source = "class Example:\n    def method(self):\n        return 3\n"
        chunks = chunker.function_aware_chunks(source, ".py")
        self.assertTrue(any(c["name"] == "Example" and "return 3" in c["text"] for c in chunks))
        self.assertTrue(any(c["name"] == "Example.method" for c in chunks))

    def test_async_function_preserves_complete_body(self):
        chunks = chunker.function_aware_chunks("async def work():\n    return await task()\n", ".py")
        self.assertEqual(chunks[0]["name"], "work")
        self.assertIn("return await task()", chunks[0]["text"])

    def test_invalid_python_requests_fallback(self):
        self.assertIsNone(chunker.function_aware_chunks("def broken(", ".py"))

    def test_unsupported_language_requests_fallback(self):
        self.assertIsNone(chunker.function_aware_chunks("plain text", ".unsupported"))

    def test_javascript_function_body_keeps_nested_block(self):
        source = "function calculate(x) { if (x) { return x + 1; } return 0; }"
        chunks = chunker.function_aware_chunks(source, ".js")
        self.assertTrue(any(c["name"] == "calculate" and "return 0;" in c["text"] for c in chunks))

if __name__ == "__main__":
    unittest.main()
