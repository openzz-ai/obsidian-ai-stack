#!/usr/bin/env python3
"""
kb-mcp-server.py — MCP server for Claude Code knowledge base integration.

Exposes 5 tools to Claude Code via the Model Context Protocol:
  - kb_search      : semantic/hybrid RAG search over your Obsidian vault
  - kb_recent      : list recently updated documents
  - context_read   : read cross-agent shared context (timeline)
  - context_write  : write notes to the shared context store
  - radar_today    : get today's radar/digest entries

Connectivity with multi-endpoint failover:
  Primary → Fallback → SSH tunnel (auto-created)

Configuration via environment variables:
  RAG_API_URL      : primary RAG service URL (default: http://YOUR_NAS_IP:8900)
  CONTEXT_API_URL  : primary Context API URL (default: http://YOUR_NAS_IP:8901)
  KB_API_KEY       : API key for X-API-Key header authentication
  NAS_SSH_ALIAS    : SSH alias for tunnel fallback (default: nas)

Example ~/.ssh/config:
  Host nas
    HostName your-nas-hostname-or-ip
    User root
    Port 22
    IdentityFile ~/.ssh/id_ed25519
"""

import os
import time
import subprocess
import threading
import httpx
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("knowledge-base")

# SSH tunnel state
_tunnel_proc = None
_tunnel_lock = threading.Lock()

NAS_SSH_ALIAS = os.environ.get("NAS_SSH_ALIAS", "nas")


def _ensure_ssh_tunnel():
    """Create SSH tunnel: localhost:19900->nas:8900, localhost:19901->nas:8901"""
    global _tunnel_proc
    with _tunnel_lock:
        if _tunnel_proc and _tunnel_proc.poll() is None:
            return True  # already running
        try:
            _tunnel_proc = subprocess.Popen(
                ["ssh", "-N", "-o", "ConnectTimeout=5", "-o", "BatchMode=yes",
                 "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=3",
                 "-L", "19900:localhost:8900",
                 "-L", "19901:localhost:8901",
                 NAS_SSH_ALIAS],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
            time.sleep(2)  # wait for tunnel to establish
            return _tunnel_proc.poll() is None
        except Exception:
            return False


# API key auth headers
KB_API_KEY = os.environ.get("KB_API_KEY", "")
_AUTH_HEADERS = {"X-API-Key": KB_API_KEY} if KB_API_KEY else {}

# Endpoint lists — customize YOUR_NAS_IP and YOUR_BACKUP_IP for your setup
_DEFAULT_RAG_PRIMARY = os.environ.get("RAG_API_URL", "http://YOUR_NAS_IP:8900")
_DEFAULT_CONTEXT_PRIMARY = os.environ.get("CONTEXT_API_URL", "http://YOUR_NAS_IP:8901")

RAG_ENDPOINTS = [
    _DEFAULT_RAG_PRIMARY,
    "http://localhost:19900",   # SSH tunnel fallback (auto-created)
]
CONTEXT_ENDPOINTS = [
    _DEFAULT_CONTEXT_PRIMARY,
    "http://localhost:19901",   # SSH tunnel fallback (auto-created)
]


def _dedup(lst):
    seen = set()
    return [x for x in lst if not (x in seen or seen.add(x))]


RAG_ENDPOINTS = _dedup(RAG_ENDPOINTS)
CONTEXT_ENDPOINTS = _dedup(CONTEXT_ENDPOINTS)

# Cached last-working endpoints
_cache: dict[str, str] = {}


def _req(service: str, method: str, path: str, **kwargs):
    """Try endpoints in priority order; cache the last working one."""
    endpoints = RAG_ENDPOINTS if service == "rag" else CONTEXT_ENDPOINTS
    cached = _cache.get(service)

    ordered = [cached] + [e for e in endpoints if e != cached] if cached else endpoints

    last_err = None
    for base in ordered:
        url = base.rstrip("/") + path
        try:
            headers = kwargs.pop("headers", {})
            headers.update(_AUTH_HEADERS)
            resp = httpx.request(method, url, timeout=8, trust_env=False, headers=headers, **kwargs)
            resp.raise_for_status()
            _cache[service] = base
            return resp
        except (httpx.ConnectError, httpx.TimeoutException) as e:
            last_err = e
            continue
        except httpx.HTTPStatusError as e:
            _cache[service] = base
            raise

    # Last resort: try SSH tunnel
    if _ensure_ssh_tunnel():
        tunnel_url = "http://localhost:19900" if service == "rag" else "http://localhost:19901"
        try:
            headers = kwargs.pop("headers", {})
            headers.update(_AUTH_HEADERS)
            resp = httpx.request(method, tunnel_url + path, timeout=8, trust_env=False, headers=headers, **kwargs)
            resp.raise_for_status()
            _cache[service] = tunnel_url
            return resp
        except Exception:
            pass

    raise ConnectionError(f"All {service} endpoints unreachable. Last error: {last_err}")


# ── Tools ──────────────────────────────────────────────────────────────────────

@mcp.tool()
def kb_search(query: str, category: str = "", tags: str = "", top_k: int = 5) -> str:
    """Search the knowledge base using semantic/RAG query."""
    body = {
        "question": query,
        "filters": {
            "category": category,
            "tags": tags.split(",") if tags else [],
        },
        "top_k": top_k,
    }
    try:
        resp = _req("rag", "POST", "/query", json=body)
        data = resp.json()
    except ConnectionError as e:
        return f"Error: {e}"
    except httpx.HTTPStatusError as e:
        return f"HTTP error {e.response.status_code}: {e.response.text}"

    results = data.get("results") or data.get("documents") or []
    if not results:
        return "No results found."

    lines = [f"**Knowledge Base Search** — `{query}`\n"]
    for i, item in enumerate(results, 1):
        title = item.get("title") or item.get("name") or "Untitled"
        cat = item.get("category") or item.get("doc_type") or ""
        snippet = item.get("snippet") or item.get("content") or item.get("text") or ""
        snippet = snippet[:300].strip()
        lines.append(f"{i}. **{title}**" + (f" [{cat}]" if cat else ""))
        if snippet:
            lines.append(f"   {snippet}")
    return "\n".join(lines)


@mcp.tool()
def kb_recent(hours: int = 24) -> str:
    """List recently updated knowledge base documents."""
    try:
        resp = _req("rag", "GET", f"/recent?hours={hours}")
        data = resp.json()
    except ConnectionError as e:
        return f"Error: {e}"
    except httpx.HTTPStatusError as e:
        return f"HTTP error {e.response.status_code}: {e.response.text}"

    docs = data.get("documents") or data.get("results") or []
    if not docs:
        return f"No documents updated in the last {hours} hours."

    lines = [f"**Recently Updated Docs** (last {hours}h)\n"]
    for item in docs:
        title = item.get("title") or item.get("name") or "Untitled"
        updated = item.get("updated_at") or item.get("created_at") or ""
        cat = item.get("category") or ""
        entry = f"- **{title}**"
        if cat:
            entry += f" [{cat}]"
        if updated:
            entry += f" — {updated}"
        lines.append(entry)
    return "\n".join(lines)


@mcp.tool()
def context_read(source: str = "", since: str = "") -> str:
    """Read agent context entries as a formatted timeline."""
    params: dict[str, str] = {}
    if source:
        params["source"] = source
    if since:
        params["since"] = since

    try:
        resp = _req("context", "GET", "/context", params=params)
        data = resp.json()
    except ConnectionError as e:
        return f"Error: {e}"
    except httpx.HTTPStatusError as e:
        return f"HTTP error {e.response.status_code}: {e.response.text}"

    entries = data.get("entries") or data.get("results") or []
    if not entries:
        return "No context entries found."

    lines = ["**Context Timeline**\n"]
    for entry in entries:
        ts = entry.get("created_at") or entry.get("timestamp") or ""
        src = entry.get("source") or ""
        content = entry.get("content") or ""
        header = f"[{ts}]" if ts else ""
        if src:
            header += f" ({src})"
        lines.append(f"{header}\n{content}\n")
    return "\n".join(lines)


@mcp.tool()
def context_write(content: str, tags: str = "", source: str = "claude-code") -> str:
    """Write a note to the agent context store."""
    body = {
        "source": source,
        "content": content,
        "tags": tags,
        "entry_type": "note",
    }
    try:
        resp = _req("context", "POST", "/context", json=body)
        data = resp.json()
    except ConnectionError as e:
        return f"Error: {e}"
    except httpx.HTTPStatusError as e:
        return f"HTTP error {e.response.status_code}: {e.response.text}"

    entry_id = data.get("id") or data.get("entry_id") or "unknown"
    return f"Context note saved (id={entry_id}, source={source})."


@mcp.tool()
def radar_today() -> str:
    """Get today's global radar summary from the context store."""
    try:
        resp = _req("context", "GET", "/context", params={"source": "radar", "since": "today"})
        data = resp.json()
    except ConnectionError as e:
        return f"Error: {e}"
    except httpx.HTTPStatusError as e:
        return f"HTTP error {e.response.status_code}: {e.response.text}"

    entries = data.get("entries") or data.get("results") or []
    if not entries:
        return "No radar entries for today."

    lines = ["**Today's Radar Summary**\n"]
    for entry in entries:
        ts = entry.get("created_at") or entry.get("timestamp") or ""
        content = entry.get("content") or ""
        if ts:
            lines.append(f"[{ts}]")
        lines.append(content)
        lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    mcp.run()
