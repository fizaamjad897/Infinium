"""
Pydantic Request/Response Models for the Infinium API
"""
from pydantic import BaseModel, Field
from typing import Optional


# ── Ingestion ──

class IngestRequest(BaseModel):
    repo_url: str = Field(..., description="GitHub repository URL (e.g., https://github.com/owner/repo)")
    repo_name: str = Field(..., description="Name for the repository (e.g., 'owner_repo')")
    branch: Optional[str] = Field(
        None,
        description="Single branch to index. If omitted and `branches` is empty, uses the repo's default branch.",
    )
    branches: Optional[list[str]] = Field(
        None,
        description=(
            "Index multiple branches in one ingest. Every file/commit/diff chunk is "
            "tagged with the branch it came from, so /api/query/cross-branch can "
            "compare them. Files unchanged across branches are embedded once "
            "(content-hash deduplication) — minimal extra cost."
        ),
    )
    callback_url: Optional[str] = Field(
        None,
        description=(
            "Optional URL to POST the final status to when ingestion finishes "
            "(complete or error). Useful for the Node.js backend to be notified "
            "without polling. Body: {status, repo_name, error?, ...stats}."
        ),
    )


class IngestLocalRequest(BaseModel):
    folder_path: str = Field(..., description="Absolute path to the local folder to ingest")
    repo_name: str = Field(..., description="Name for the repository")
    branch: Optional[str] = Field(None, description="Single branch to checkout + index (if folder is a git repo)")
    branches: Optional[list[str]] = Field(None, description="Multi-branch indexing (see IngestRequest.branches)")
    callback_url: Optional[str] = Field(None, description="POST'd when ingestion finishes")


class RemoteIngestRequest(BaseModel):
    repo_url: str = Field(..., description="Public GitHub repo URL (e.g., https://github.com/owner/repo)")
    repo_name: str = Field(..., description="Name for the repository in the index")
    branch: Optional[str] = Field(None, description="Single branch to index (default: repo's default branch)")
    branches: Optional[list[str]] = Field(
        None,
        description="Multi-branch indexing — downloads one tarball per branch and tags chunks with branch metadata.",
    )
    github_token: Optional[str] = Field(None, description="GitHub token for private repos or higher rate limits (5000/hr vs 60/hr)")
    callback_url: Optional[str] = Field(None, description="POST'd when ingestion finishes")



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
    use_dual_model: Optional[bool] = Field(None, description="Use dual-model pipeline (None = use config default)")
    top_k: Optional[int] = Field(None, description="Number of context chunks to retrieve")
    model: Optional[str] = Field(None, description="Override LLM model")
    conversation_id: Optional[str] = Field(None, description="Conversation ID for follow-up questions")
    # Git-aware filters
    branch_filter: Optional[str] = Field(None, description="Scope search to a specific branch name (e.g. 'feature-auth')")
    chunk_types: Optional[list[str]] = Field(
        None,
        description="Filter chunk types: 'code', 'commit', 'commit_diff', 'branch'. None = all types.",
    )


class QueryAllRequest(BaseModel):
    query: str = Field(..., description="Natural language question about the codebase")
    repo_names: Optional[list[str]] = Field(None, description="List of repos to search (None = ALL repos)")
    use_hybrid: bool = Field(True, description="Use hybrid search")
    use_expansion: bool = Field(False, description="Use LLM query expansion")
    use_dual_model: Optional[bool] = Field(None, description="Use dual-model pipeline (None = use config default)")
    top_k: Optional[int] = Field(None, description="Number of chunks to retrieve across all repos")
    model: Optional[str] = Field(None, description="Override LLM model")
    conversation_id: Optional[str] = Field(None, description="Conversation ID for follow-up questions")
    branch_filter: Optional[str] = Field(None, description="Scope search to a specific branch name across all repos")
    chunk_types: Optional[list[str]] = Field(None, description="Filter chunk types")


class GitQueryRequest(BaseModel):
    """Dedicated git history query — focuses only on commits, diffs, and branches."""
    query: str = Field(..., description="Natural language question about git history")
    repo_name: str = Field(..., description="Repository to query")
    branch_filter: Optional[str] = Field(None, description="Scope to a specific branch name")
    include_diffs: bool = Field(True, description="Include commit diff patches in context")
    include_commits: bool = Field(True, description="Include commit messages/metadata")
    include_branches: bool = Field(True, description="Include branch metadata")
    top_k: Optional[int] = Field(None, description="Number of chunks to retrieve")
    use_dual_model: Optional[bool] = Field(None, description="Use dual-model pipeline")


class SourceInfo(BaseModel):
    source: str
    repo: str = ""
    type: str = "code"
    relevance: float = 0.0


class ReasoningStepResponse(BaseModel):
    step_name: str
    model_used: str = ""
    duration_ms: int = 0
    input_preview: str = ""
    output_preview: str = ""
    chunks_count: int = 0
    status: str = "complete"


class ReasoningTraceResponse(BaseModel):
    trace_id: str = ""
    query: str = ""
    steps: list[ReasoningStepResponse] = []
    total_duration_ms: int = 0
    models_used: list[str] = []


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
    reasoning_trace: Optional[ReasoningTraceResponse] = None


# ── Conversation ──

class ConversationStartRequest(BaseModel):
    repo_name: str = Field("", description="Repository context for this conversation")


class ConversationStartResponse(BaseModel):
    conversation_id: str
    message: str = "Conversation started"


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


# ── Git History ──

class BranchInfo(BaseModel):
    name: str
    last_commit_sha: str = ""
    last_commit_author: str = ""
    last_commit_date: str = ""
    last_commit_message: str = ""
    is_default: bool = False
    merged: bool = False


class CommitInfo(BaseModel):
    sha: str
    message: str
    author: str = ""
    date: str = ""
    branch: str = ""
    files_changed: list[str] = []
    has_diff: bool = False


class GitHistoryResponse(BaseModel):
    repo_name: str
    branches: list[BranchInfo] = []
    commits: list[CommitInfo] = []
    total_branches: int = 0
    total_commits: int = 0