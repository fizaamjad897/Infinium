"""
Slack Bot Integration (FR-010)

Three flavours of Slack ingress, each as its own modular endpoint:

  POST /api/slack/command   → Slash command receiver (e.g. `/infinium how is auth handled?`)
  POST /api/slack/events    → Events API receiver — handles app_mention (e.g. "@Infinium why...")
  POST /api/slack/test      → Local-only smoke test that bypasses Slack signature verification

Slack request signing (https://api.slack.com/authentication/verifying-requests-from-slack)
is enforced when ``SLACK_SIGNING_SECRET`` is set in the environment.
"""
from __future__ import annotations
import os
import time
import hmac
import hashlib
import json
from typing import Optional

import httpx
from fastapi import APIRouter, Request, BackgroundTasks, HTTPException, Header, Form
from pydantic import BaseModel, Field

from backend.rag.query_engine import ask_all, ask
from backend.config import settings

router = APIRouter(prefix="/api/slack", tags=["Slack"])


# ── Signature verification ──

def _verify_slack_signature(body: bytes, ts: str, signature: str) -> bool:
    """Confirm the request came from Slack using HMAC-SHA256 of "v0:ts:body"."""
    secret = os.getenv("SLACK_SIGNING_SECRET", "")
    if not secret:
        # No secret configured → skip verification (dev mode). Loud warning logged elsewhere.
        return True
    if not ts or not signature:
        return False
    # Reject replays older than 5 minutes
    try:
        if abs(time.time() - int(ts)) > 60 * 5:
            return False
    except ValueError:
        return False

    base = f"v0:{ts}:{body.decode('utf-8', errors='ignore')}".encode()
    expected = "v0=" + hmac.new(secret.encode(), base, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


# ── Posting back to Slack ──

async def _post_to_slack(response_url: str, text: str, blocks: Optional[list] = None):
    """Send a delayed response to Slack via the response_url."""
    payload = {"text": text, "response_type": "in_channel"}
    if blocks:
        payload["blocks"] = blocks
    async with httpx.AsyncClient() as client:
        try:
            await client.post(response_url, json=payload, timeout=15)
        except Exception as e:
            print(f"   ⚠️ Failed to post to Slack: {e}")


async def _post_thread_reply(channel: str, thread_ts: str, text: str):
    """Post a threaded reply via the Slack Web API (chat.postMessage)."""
    bot_token = os.getenv("SLACK_BOT_TOKEN", "")
    if not bot_token:
        print("   ⚠️ SLACK_BOT_TOKEN not configured — cannot post thread reply")
        return
    async with httpx.AsyncClient() as client:
        try:
            await client.post(
                "https://slack.com/api/chat.postMessage",
                headers={
                    "Authorization": f"Bearer {bot_token}",
                    "Content-Type": "application/json",
                },
                json={"channel": channel, "thread_ts": thread_ts, "text": text},
                timeout=15,
            )
        except Exception as e:
            print(f"   ⚠️ Failed to post thread reply: {e}")


# ── Answer formatter ──

def _format_answer_for_slack(result: dict) -> str:
    """Compress an answer + sources list into a Slack-friendly message."""
    answer = result.get("answer", "(no answer)")
    sources = result.get("sources", []) or []
    src_lines = []
    for s in sources[:5]:
        repo = s.get("repo", "")
        path = s.get("source", "")
        if repo and path:
            src_lines.append(f"• `{repo}` — `{path}`")
        elif path:
            src_lines.append(f"• `{path}`")
    sources_block = ("\n*Sources:*\n" + "\n".join(src_lines)) if src_lines else ""
    return f"{answer}{sources_block}"


# ── Background workers ──

async def _process_slash_command(query: str, repo: str | None, response_url: str, user: str):
    """Run the query and post the answer back to Slack."""
    try:
        if repo:
            result = ask(query=query, repo_name=repo, user_login=user, interface="slack")
        else:
            result = ask_all(query=query, user_login=user, interface="slack")
        await _post_to_slack(response_url, _format_answer_for_slack(result))
    except Exception as e:
        await _post_to_slack(response_url, f":warning: Infinium error: {e}")


async def _process_app_mention(query: str, channel: str, thread_ts: str, user: str):
    try:
        result = ask_all(query=query, user_login=user, interface="slack")
        await _post_thread_reply(channel, thread_ts, _format_answer_for_slack(result))
    except Exception as e:
        await _post_thread_reply(channel, thread_ts, f":warning: Infinium error: {e}")


# ── Endpoints ──

@router.post("/command")
async def slack_slash_command(
    request: Request,
    background_tasks: BackgroundTasks,
    x_slack_request_timestamp: Optional[str] = Header(None),
    x_slack_signature: Optional[str] = Header(None),
):
    """Slack slash command receiver.

    Configure in Slack: ``Slash Commands → Request URL`` →
    ``https://<your-host>/api/slack/command``. Slack POSTs
    ``application/x-www-form-urlencoded`` bodies, so we read the raw body for
    signature verification then re-parse it.

    Forms supported:
      ``/infinium <question>``
      ``/infinium repo:<repo_name> <question>``
    """
    raw = await request.body()
    if not _verify_slack_signature(raw, x_slack_request_timestamp or "", x_slack_signature or ""):
        raise HTTPException(status_code=401, detail="Invalid Slack signature")

    form = dict((await request.form()))
    text = (form.get("text") or "").strip()
    response_url = form.get("response_url") or ""
    user_name = form.get("user_name") or "slack-user"

    if not text:
        return {
            "response_type": "ephemeral",
            "text": "Usage: `/infinium <question>` or `/infinium repo:my-repo <question>`",
        }

    # Extract optional `repo:NAME` prefix
    repo_name: Optional[str] = None
    if text.startswith("repo:"):
        head, _, rest = text.partition(" ")
        repo_name = head.removeprefix("repo:").strip() or None
        text = rest.strip()

    if not text:
        return {"response_type": "ephemeral", "text": "Please include a question after the repo name."}

    # Slack requires a 200 within 3s — defer the real work and stream back via response_url.
    background_tasks.add_task(_process_slash_command, text, repo_name, response_url, user_name)
    return {
        "response_type": "ephemeral",
        "text": f":mag: Asking Infinium" + (f" about *{repo_name}*" if repo_name else "") + "…",
    }


@router.post("/events")
async def slack_events(
    request: Request,
    background_tasks: BackgroundTasks,
    x_slack_request_timestamp: Optional[str] = Header(None),
    x_slack_signature: Optional[str] = Header(None),
):
    """Slack Events API receiver — handles ``url_verification`` and ``app_mention``."""
    raw = await request.body()
    if not _verify_slack_signature(raw, x_slack_request_timestamp or "", x_slack_signature or ""):
        raise HTTPException(status_code=401, detail="Invalid Slack signature")

    payload = json.loads(raw or b"{}")

    # Slack URL verification challenge
    if payload.get("type") == "url_verification":
        return {"challenge": payload.get("challenge", "")}

    event = payload.get("event", {}) or {}
    if event.get("type") == "app_mention":
        text = (event.get("text") or "").strip()
        # Strip the bot mention prefix "<@U123ABC>"
        if text.startswith("<@"):
            _, _, rest = text.partition(">")
            text = rest.strip()
        channel = event.get("channel", "")
        thread_ts = event.get("thread_ts") or event.get("ts", "")
        user = event.get("user", "slack-user")

        if text and channel and thread_ts:
            background_tasks.add_task(_process_app_mention, text, channel, thread_ts, user)

    # Always 200 — Slack retries on non-2xx
    return {"ok": True}


# ── Local smoke test ──

class SlackTestRequest(BaseModel):
    query: str = Field(..., description="Question to ask via Slack-style flow")
    repo_name: Optional[str] = Field(None, description="Optional repo scope")


@router.post("/test")
async def slack_test_locally(req: SlackTestRequest):
    """Local-only smoke test — runs the Slack flow without a real Slack request.

    Returns the formatted Slack message so you can paste it into Slack to see
    rendering. Skip if you want to test the full webhook path (use a tunnel
    like ngrok and hit /api/slack/command instead).
    """
    if req.repo_name:
        result = ask(query=req.query, repo_name=req.repo_name,
                     user_login="slack-test", interface="slack")
    else:
        result = ask_all(query=req.query, user_login="slack-test", interface="slack")
    return {
        "slack_text": _format_answer_for_slack(result),
        "raw_answer": result.get("answer", ""),
        "sources": result.get("sources", []),
    }
