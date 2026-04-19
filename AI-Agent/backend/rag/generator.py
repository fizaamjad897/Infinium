"""
LLM Response Generator — Dual-Model Architecture
- Step 1 (optional): Code reasoning via kimi-k2.5 (deep analysis)
- Step 2: Final synthesis via minimax-m2.7 (polished answer)
- Uses Ollama /api/chat endpoint (required for cloud models)
- Includes reasoning trace capture
- Supports conversation history for follow-ups
"""
import requests
import time
from typing import Optional
from backend.config import settings
from backend.rag.reasoning import ReasoningTrace, StepTimer


# ── System Prompts ──

REASONING_SYSTEM_PROMPT = """You are an expert code analyst. Your job is to deeply analyze code, commits, and documentation to understand WHY and HOW decisions were made in a software project.

Given the context from a codebase, perform the following analysis:
1. TRACE the causal chain — what led to this code/decision?
2. IDENTIFY the key files, functions, and commits involved
3. EXPLAIN the temporal sequence — what happened first, what followed
4. NOTE any patterns, trade-offs, or architectural implications
5. HIGHLIGHT connections between different parts of the codebase

Be thorough and analytical. Your output will be used by another AI to write the final answer.
Focus on REASONING, not on formatting the output prettily."""

SYNTHESIS_SYSTEM_PROMPT = """You are Infinium, an expert AI reasoning agent designed to help developers understand their entire codebase.
You have access to code files, commit history, issues, pull requests, and documentation from the user's repositories.

When answering questions:
1. **Always cite your sources** — reference as [Source N] (e.g., [Source 1: path/to/file.py])
2. **Explain WHY** decisions were made, not just what they are — trace through commit history and code changes
3. **Cross-reference across repos** when the context spans multiple repositories
4. **Be precise and technical** — provide specific code references, function names, and commit SHAs
5. **Structure your response** clearly with headers and bullet points for complex questions
6. **If context is insufficient**, say so clearly and suggest what additional information would help

You are NOT a code generator. You are a reasoning agent that helps developers understand their codebase's history, architecture, and decision-making patterns."""

SINGLE_MODEL_SYSTEM_PROMPT = SYNTHESIS_SYSTEM_PROMPT  # Same prompt for single-model mode


def _call_ollama_chat(
    model: str,
    messages: list[dict],
    temperature: float = 0.1,
    context_window: int = 8192,
    timeout: int = 300,
    retries: int = 2,
) -> dict:
    """Call Ollama /api/chat endpoint with retry on transient errors.

    Returns: {"content": str, "tokens": int, "duration_ms": int}
    """
    import time
    last_error = None
    for attempt in range(retries + 1):
        try:
            response = requests.post(
                f"{settings.OLLAMA_BASE_URL}/api/chat",
                json={
                    "model": model,
                    "messages": messages,
                    "stream": False,
                    "options": {
                        "temperature": temperature,
                        "num_ctx": context_window,
                    },
                },
                timeout=timeout,
            )
            response.raise_for_status()
            result = response.json()

            return {
                "content": result.get("message", {}).get("content", ""),
                "tokens": result.get("eval_count", 0),
                "duration_ms": result.get("total_duration", 0) // 1_000_000,
            }
        except requests.exceptions.Timeout:
            return {"content": "", "tokens": 0, "duration_ms": 0, "error": "Request timed out"}
        except requests.exceptions.ConnectionError:
            return {
                "content": "",
                "tokens": 0,
                "duration_ms": 0,
                "error": f"Cannot connect to Ollama at {settings.OLLAMA_BASE_URL}. Make sure Ollama is running.",
            }
        except Exception as e:
            last_error = str(e)
            if attempt < retries:
                time.sleep(3 * (attempt + 1))  # 3s, 6s backoff
                continue
            return {"content": "", "tokens": 0, "duration_ms": 0, "error": last_error}
    return {"content": "", "tokens": 0, "duration_ms": 0, "error": last_error}



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


def _generate_dual_model(
    query: str,
    chunks: list[dict],
    context: str,
    trace: ReasoningTrace,
    conversation_context: str = "",
) -> dict:
    """Dual-model pipeline: kimi reasons → minimax synthesizes.

    Step 1: kimi-k2.5 analyzes the code context deeply
    Step 2: minimax-m2.7 produces a polished, cited answer
    """
    reasoning_model = settings.CODE_REASONING_MODEL
    synthesis_model = settings.LLM_MODEL
    repos_in_context = set(c.get("repo", "unknown") for c in chunks)
    total_tokens = 0

    # ── Step 1: Code Reasoning (kimi-k2.5) ──
    reasoning_output = ""
    with StepTimer(trace, "code_reasoning", reasoning_model) as timer:
        reasoning_prompt = f"""Analyze the following codebase context to answer this question:

Question: {query}

Codebase Context:
{context}
{f"Conversation History: {conversation_context}" if conversation_context else ""}

Provide a thorough analysis focusing on:
- What the code does and why it was written this way
- Key commits and their motivation
- Architectural decisions and trade-offs
- Connections between different files/components"""

        timer.input_text = reasoning_prompt
        timer.chunks_count = len(chunks)

        result = _call_ollama_chat(
            model=reasoning_model,
            messages=[
                {"role": "system", "content": REASONING_SYSTEM_PROMPT},
                {"role": "user", "content": reasoning_prompt},
            ],
            temperature=settings.REASONING_TEMPERATURE,
        )

        if result.get("error"):
            # Fallback: skip reasoning step, go straight to synthesis
            print(f"   ⚠️ Reasoning model failed: {result['error']}. Falling back to single-model.")
            timer.status = "error"
            timer.output_text = result["error"]
            reasoning_output = ""
        else:
            reasoning_output = result["content"]
            total_tokens += result["tokens"]
            timer.output_text = reasoning_output

    # ── Step 2: Synthesis (minimax-m2.7) ──
    with StepTimer(trace, "synthesis", synthesis_model) as timer:
        # Build the synthesis prompt with reasoning output
        repo_hint = ""
        if len(repos_in_context) > 1:
            repo_hint = f"\nNote: Context spans {len(repos_in_context)} repositories: {', '.join(repos_in_context)}. Cross-reference insights across repos when relevant."

        if reasoning_output:
            synthesis_prompt = f"""Based on the expert code analysis below and the original codebase context, provide a clear, well-structured answer.

Expert Analysis:
{reasoning_output}

Original Codebase Context:
{context}
{repo_hint}
{f"Previous conversation: {conversation_context}" if conversation_context else ""}

Question: {query}

Provide a comprehensive answer. Cite sources as [Source N] based on the original context numbering."""
        else:
            # No reasoning output (model failed) — direct synthesis
            synthesis_prompt = f"""Context from the codebase:
{context}
{repo_hint}
{f"Previous conversation: {conversation_context}" if conversation_context else ""}

Question: {query}

Please answer based on the context above. Cite sources by their [Source N] number. If the question asks 'why', trace through the commit history and code evolution to explain the reasoning."""

        timer.input_text = synthesis_prompt
        timer.chunks_count = len(chunks)

        result = _call_ollama_chat(
            model=synthesis_model,
            messages=[
                {"role": "system", "content": SYNTHESIS_SYSTEM_PROMPT},
                {"role": "user", "content": synthesis_prompt},
            ],
            temperature=settings.LLM_TEMPERATURE,
        )

        if result.get("error"):
            timer.status = "error"
            timer.output_text = result["error"]
            return {
                "answer": f"Error generating response: {result['error']}\n\n"
                          f"Make sure Ollama is running and the models are pulled:\n"
                          f"  ollama run {synthesis_model}\n"
                          f"  ollama run {reasoning_model}",
                "sources": [],
                "model": synthesis_model,
                "tokens_used": total_tokens,
                "error": result["error"],
            }

        total_tokens += result["tokens"]
        timer.output_text = result["content"]

    return {
        "answer": result["content"],
        "sources": [
            {
                "source": c.get("source", "unknown"),
                "repo": c.get("repo", "unknown"),
                "type": c.get("type", "code"),
                "relevance": c.get("relevance", 0),
            }
            for c in chunks
        ],
        "model": f"{reasoning_model} + {synthesis_model}",
        "tokens_used": total_tokens,
        "total_duration_ms": result.get("duration_ms", 0),
    }


def _generate_single_model(
    query: str,
    chunks: list[dict],
    context: str,
    model: str,
    trace: ReasoningTrace,
    conversation_context: str = "",
) -> dict:
    """Single-model pipeline — one LLM does everything."""
    repos_in_context = set(c.get("repo", "unknown") for c in chunks)

    with StepTimer(trace, "generation", model) as timer:
        repo_hint = ""
        if len(repos_in_context) > 1:
            repo_hint = f"\nNote: Context spans {len(repos_in_context)} repositories: {', '.join(repos_in_context)}. Cross-reference insights across repos when relevant."

        user_message = f"""Context from the codebase:
{context}
{repo_hint}
{f"Previous conversation: {conversation_context}" if conversation_context else ""}

Question: {query}

Please answer based on the context above. Cite sources by their [Source N] number. If the question asks 'why', trace through the commit history and code evolution to explain the reasoning."""

        timer.input_text = user_message
        timer.chunks_count = len(chunks)

        result = _call_ollama_chat(
            model=model,
            messages=[
                {"role": "system", "content": SINGLE_MODEL_SYSTEM_PROMPT},
                {"role": "user", "content": user_message},
            ],
            temperature=settings.LLM_TEMPERATURE,
        )

        if result.get("error"):
            timer.status = "error"
            timer.output_text = result["error"]
            return {
                "answer": f"Error generating response: {result['error']}\n\n"
                          f"Make sure Ollama is running (`ollama serve`) and the model is available:\n"
                          f"  ollama run {model}",
                "sources": [],
                "model": model,
                "tokens_used": 0,
                "error": result["error"],
            }

        timer.output_text = result["content"]

    return {
        "answer": result["content"],
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
        "tokens_used": result.get("tokens", 0),
        "total_duration_ms": result.get("duration_ms", 0),
    }


def generate_response(
    query: str,
    chunks: list[dict],
    model: Optional[str] = None,
    use_dual_model: Optional[bool] = None,
    conversation_context: str = "",
    trace: Optional[ReasoningTrace] = None,
) -> dict:
    """Generate an LLM response with injected codebase context.

    Supports two modes:
    - Dual-model: kimi reasons about code → minimax writes final answer
    - Single-model: one model does everything

    Args:
        query: The user's natural language question
        chunks: Retrieved context chunks from the RAG retriever
        model: Override LLM model (forces single-model mode)
        use_dual_model: Override dual-model setting
        conversation_context: Previous conversation turns for follow-ups
        trace: ReasoningTrace to record steps into

    Returns:
        dict with answer, sources, model, tokens_used, reasoning_trace
    """
    if trace is None:
        trace = ReasoningTrace()
        trace.start(query)

    context = build_context_block(chunks)

    # Decide which pipeline to use
    if model:
        # Explicit model override → single-model mode
        result = _generate_single_model(
            query, chunks, context, model, trace, conversation_context
        )
    elif use_dual_model if use_dual_model is not None else settings.USE_DUAL_MODEL:
        # Dual-model pipeline
        result = _generate_dual_model(
            query, chunks, context, trace, conversation_context
        )
    else:
        # Single-model with default
        result = _generate_single_model(
            query, chunks, context, settings.LLM_MODEL, trace, conversation_context
        )

    trace.finish()
    result["reasoning_trace"] = trace.to_dict()
    return result
