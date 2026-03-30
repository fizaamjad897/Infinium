"""
Pydantic Request/Response Models for the Infinium API
"""
from pydantic import BaseModel, Field
from typing import Optional


# ── Ingestion ──

class IngestRequest(BaseModel):
    repo_url: str = Field(..., description="GitHub repository URL (e.g., https://github.com/owner/repo)")
    repo_name: str = Field(..., description="Name for the repository (e.g., 'owner_repo')")


class IngestLocalRequest(BaseModel):
    folder_path: str = Field(..., description="Absolute path to the local folder to ingest")
    repo_name: str = Field(..., description="Name for the repository")


class IngestResponse(BaseModel):
    status: str
    repo_name: str
    message: str = ""


# ── Query ──

class QueryRequest(BaseModel):
    query: str = Field(..., description="Natural language question about the codebase")
    repo_name: str = Field(..., description="Repository to search in")
    use_hybrid: bool = Field(True, description="Use hybrid (semantic + keyword) search")
    use_expansion: bool = Field(False, description="Use LLM query expansion")
    top_k: Optional[int] = Field(None, description="Number of context chunks to retrieve")
    model: Optional[str] = Field(None, description="Override LLM model")


class QueryAllRequest(BaseModel):
    query: str = Field(..., description="Natural language question about the codebase")
    repo_names: Optional[list[str]] = Field(None, description="List of repos to search (None = ALL repos)")
    use_hybrid: bool = Field(True, description="Use hybrid search")
    use_expansion: bool = Field(False, description="Use LLM query expansion")
    top_k: Optional[int] = Field(None, description="Number of chunks to retrieve across all repos")
    model: Optional[str] = Field(None, description="Override LLM model")


class SourceInfo(BaseModel):
    source: str
    repo: str = ""
    type: str = "code"
    relevance: float = 0.0


class QueryResponse(BaseModel):
    answer: str
    sources: list[SourceInfo] = []
    model: str = ""
    tokens_used: int = 0
    query_used: str = ""
    chunks_retrieved: int = 0
    repos_searched: Optional[list[str]] = None
    repos_with_results: Optional[list[str]] = None
    total_duration_ms: int = 0


# ── Feedback ──

class FeedbackRequest(BaseModel):
    query: str = Field(..., description="The original query")
    answer: str = Field(..., description="The generated answer")
    rating: int = Field(..., ge=1, le=5, description="Rating from 1 (bad) to 5 (excellent)")
    comment: str = Field("", description="Optional textual feedback")
    repo_name: str = Field("", description="Which repo was queried")


# ── Repos ──

class RepoStatusResponse(BaseModel):
    repo_name: str
    status: str
    chunks: int = 0
    files_processed: int = 0
    commits_processed: int = 0
    repo_url: str = ""
    folder_path: str = ""
    updated_at: str = ""
