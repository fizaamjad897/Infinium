"""
LLM Response Generator
- Builds context blocks from retrieved chunks
- Calls Ollama LLM with Infinium persona
- Returns cited, reasoned answers
"""
import requests
from backend.config import settings


SYSTEM_PROMPT = """You are Infinium, an expert AI reasoning agent designed to help developers understand their entire codebase.
You have access to code files, commit history, issues, pull requests, and documentation from the user's repositories.

When answering questions:
1. **Always cite your sources** — reference file paths or commit SHAs (e.g., [Source 1: path/to/file.py])
2. **Explain WHY** decisions were made, not just what they are — trace through commit history and code changes
3. **Cross-reference across repos** when relevant — you have access to the user's entire codebase
4. **Be precise and technical** — provide specific code references, line numbers, and function names when possible
5. **If you don't have enough context**, say so clearly and suggest what additional information would help
6. **Structure your response** with clear sections for complex questions

You are NOT a code generator. You are a reasoning agent that helps developers understand their codebase's history, architecture, and decision-making patterns."""


def build_context_block(chunks: list[dict]) -> str:
    """Format retrieved chunks into a labeled context block for the LLM."""
    if not chunks:
        return "No relevant context found."
    
    parts = []
    for i, chunk in enumerate(chunks, 1):
        repo = chunk.get("repo", "unknown")
        source = chunk.get("source", "unknown")
        chunk_type = chunk.get("type", "code")
        relevance = chunk.get("relevance", 0)
        
        header = f"[Source {i}: {repo}/{source} (type: {chunk_type}, relevance: {relevance})]"
        parts.append(header)
        parts.append(chunk["text"])
        parts.append("")
    
    return "\n".join(parts)


def generate_response(
    query: str,
    chunks: list[dict],
    model: str = None,
    stream: bool = False,
) -> dict:
    """Generate an LLM response with injected codebase context.
    
    Args:
        query: The user's natural language question
        chunks: Retrieved context chunks from the RAG retriever
        model: LLM model to use (defaults to config)
        stream: Whether to stream the response (not yet implemented)
    
    Returns:
        dict with answer, sources, model, tokens_used
    """
    model = model or settings.LLM_MODEL
    context = build_context_block(chunks)
    
    # Determine if multi-repo context
    repos_in_context = set(c.get("repo", "unknown") for c in chunks)
    repo_hint = ""
    if len(repos_in_context) > 1:
        repo_hint = f"\nNote: The context below spans {len(repos_in_context)} repositories: {', '.join(repos_in_context)}. Cross-reference insights across repos when relevant."
    
    user_message = f"""Context from the codebase:
{context}
{repo_hint}
Question: {query}

Please answer based on the context above. Cite sources by their [Source N] number. If the question asks 'why', trace through the commit history and code evolution to explain the reasoning."""

    try:
        response = requests.post(
            f"{settings.OLLAMA_BASE_URL}/api/generate",
            json={
                "model": model,
                "prompt": user_message,
                "system": SYSTEM_PROMPT,
                "stream": False,
                "options": {
                    "temperature": settings.LLM_TEMPERATURE,
                    "num_ctx": settings.LLM_CONTEXT_WINDOW,
                },
            },
            timeout=300,
        )
        response.raise_for_status()
        result = response.json()
        
        return {
            "answer": result.get("response", "No response generated."),
            "sources": [
                {
                    "source": c.get("source", "unknown"),
                    "repo": c.get("repo", "unknown"),
                    "type": c.get("type", "code"),
                    "relevance": c.get("relevance", 0),
                }
                for c in chunks
            ],
            "model": model,
            "tokens_used": result.get("eval_count", 0),
            "total_duration_ms": result.get("total_duration", 0) // 1_000_000,
        }
    except Exception as e:
        return {
            "answer": f"Error generating response: {e}\n\nMake sure Ollama is running (`ollama serve`) and the model '{model}' is pulled (`ollama pull {model}`).",
            "sources": [],
            "model": model,
            "tokens_used": 0,
            "error": str(e),
        }
