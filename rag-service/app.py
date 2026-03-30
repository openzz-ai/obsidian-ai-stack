import os
import asyncio
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import httpx
import yaml
import chromadb
from fastapi import FastAPI, BackgroundTasks, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

# ---------------------------------------------------------------------------
# Configuration — override via environment variables
# ---------------------------------------------------------------------------
VAULT_PATH = Path(os.getenv("VAULT_PATH", "/data/vault"))
CHROMADB_PATH = os.getenv("CHROMADB_PATH", "/data/chromadb")
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://host.docker.internal:11434")
EMBED_MODEL = "nomic-embed-text"
CHAT_MODEL = os.getenv("OLLAMA_CHAT_MODEL", "qwen2.5:7b-instruct-q4_K_M")
COLLECTION_NAME = "vault_docs"
SKIP_DIRS = {".obsidian", ".claude", ".backups"} | set(s for s in os.environ.get("SKIP_DIRS_EXTRA", "").split(",") if s)

# Security — set KB_API_KEY env var to enable API key authentication
API_KEY = os.environ.get("KB_API_KEY", "")
AUDIT_LOG = os.environ.get("AUDIT_LOG_PATH", "/data/audit.log")

# ---------------------------------------------------------------------------
# ChromaDB client (module-level, shared)
# ---------------------------------------------------------------------------
chroma_client = chromadb.PersistentClient(path=CHROMADB_PATH)
collection = chroma_client.get_or_create_collection(
    name=COLLECTION_NAME,
    metadata={"hnsw:space": "cosine"},
)

# ---------------------------------------------------------------------------
# Index state
# ---------------------------------------------------------------------------
index_state = {
    "status": "idle",
    "last_indexed": None,
    "docs_count": 0,
}

# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------
app = FastAPI(title="Vault RAG Service", version="1.0.0")


# ---------------------------------------------------------------------------
# Security middleware
# ---------------------------------------------------------------------------
def _audit(client_ip: str, method: str, path: str, status: int) -> None:
    ts = datetime.now(tz=timezone.utc).isoformat()
    line = f"{ts} {client_ip} {method} {path} {status}\n"
    try:
        with open(AUDIT_LOG, "a") as f:
            f.write(line)
    except Exception:
        pass


@app.middleware("http")
async def auth_and_audit(request: Request, call_next):
    # Health check skips auth
    if request.url.path == "/health":
        return await call_next(request)
    # API key check
    if API_KEY and request.headers.get("X-API-Key") != API_KEY:
        client_ip = request.client.host if request.client else "unknown"
        _audit(client_ip, request.method, request.url.path, 401)
        return JSONResponse(status_code=401, content={"error": "unauthorized"})
    response = await call_next(request)
    client_ip = request.client.host if request.client else "unknown"
    _audit(client_ip, request.method, request.url.path, response.status_code)
    return response


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------
class QueryFilters(BaseModel):
    category: Optional[str] = None
    tags: Optional[list[str]] = None


class QueryRequest(BaseModel):
    question: str
    filters: Optional[QueryFilters] = None
    top_k: int = 5
    with_llm: bool = False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _derive_category(rel_path: Path) -> str:
    """Derive category from vault folder structure when frontmatter has none.

    Customize this mapping to match your own Obsidian vault structure.
    """
    parts = rel_path.parts
    if not parts:
        return "note"
    top = parts[0]
    sub = parts[1] if len(parts) >= 2 else ""

    # Example mappings — adapt to your vault layout
    # top_folder → category mapping
    top_map = {
        "Projects": "project",
        "Archive": "archive",
        "Daily": "daily",
        "Literature": "literature",
    }
    if top in top_map:
        return top_map[top]

    return "note"


def _doc_id(path: Path) -> str:
    return hashlib.md5(str(path).encode()).hexdigest()


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    if not text.startswith("---"):
        return {}, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text
    try:
        fm = yaml.safe_load(parts[1]) or {}
        if not isinstance(fm, dict):
            fm = {}
    except yaml.YAMLError:
        fm = {}
    return fm, parts[2].strip()


async def _llm_l0(title: str, body: str) -> str:
    """Generate a one-sentence L0 summary via Ollama chat model.

    Called once per document at index time; result is cached in ChromaDB metadata.
    Subsequent reindex calls skip documents that already have a summary.
    """
    prompt = (
        "Summarize the following document in one sentence (max 25 words). "
        "Output the summary directly, no preamble.\n"
        f"Title: {title}\nContent: {body[:1200]}"
    )
    try:
        async with httpx.AsyncClient(timeout=25.0) as client:
            resp = await client.post(
                f"{OLLAMA_URL}/api/chat",
                json={
                    "model": CHAT_MODEL,
                    "messages": [{"role": "user", "content": prompt}],
                    "stream": False,
                    "options": {"num_predict": 50, "temperature": 0},
                },
            )
            resp.raise_for_status()
            return resp.json()["message"]["content"].strip()
    except Exception as exc:
        print(f"[l0] failed for '{title}': {exc}")
        return ""


async def _expand_query(question: str) -> list[str]:
    """Expand a short/ambiguous query into 2 semantically related variants.

    Runs concurrently with the main semantic search to avoid added latency.
    Customize the domain description in the prompt to match your knowledge base.
    """
    prompt = (
        "This is a personal knowledge base (customize this description for your domain). "
        "Expand the following search query into 2 related search phrases to improve recall. "
        "Output one phrase per line, no numbering or explanation.\n"
        f"Query: {question}"
    )
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{OLLAMA_URL}/api/chat",
                json={
                    "model": CHAT_MODEL,
                    "messages": [{"role": "user", "content": prompt}],
                    "stream": False,
                    "options": {"num_predict": 60, "temperature": 0.2},
                },
            )
            resp.raise_for_status()
            content = resp.json()["message"]["content"].strip()
            lines = [l.strip() for l in content.splitlines() if l.strip()]
            return lines[:2]
    except Exception as exc:
        print(f"[expand] failed for '{question}': {exc}")
        return []


async def _embed(text: str) -> list[float]:
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(
            f"{OLLAMA_URL}/api/embeddings",
            json={"model": EMBED_MODEL, "prompt": text},
        )
        resp.raise_for_status()
        return resp.json()["embedding"]


def _build_where_clause(filters: Optional[QueryFilters]) -> Optional[dict]:
    if not filters:
        return None
    conditions = []
    if filters.category:
        conditions.append({"category": {"$eq": filters.category}})
    if filters.tags:
        for tag in filters.tags:
            conditions.append({"tags": {"$contains": tag}})
    if not conditions:
        return None
    if len(conditions) == 1:
        return conditions[0]
    return {"$and": conditions}


# ---------------------------------------------------------------------------
# Indexer
# ---------------------------------------------------------------------------
async def _index_vault():
    if index_state["status"] == "indexing":
        return
    index_state["status"] = "indexing"
    count = 0
    try:
        md_files = [
            p for p in VAULT_PATH.rglob("*.md")
            if not any(skip in p.parts for skip in SKIP_DIRS)
        ]
        for md_path in md_files:
            try:
                text = md_path.read_text(encoding="utf-8", errors="ignore")
                fm, body = _parse_frontmatter(text)

                title = fm.get("title") or md_path.stem
                rel = md_path.relative_to(VAULT_PATH)
                category = str(fm.get("category", "")) or _derive_category(rel)
                raw_tags = fm.get("tags", [])
                tags = raw_tags if isinstance(raw_tags, list) else [str(raw_tags)]
                tags_str = ",".join(str(t) for t in tags)

                updated_ts = md_path.stat().st_mtime
                updated_iso = datetime.fromtimestamp(updated_ts, tz=timezone.utc).isoformat()

                # Generate L0 one-sentence summary (skip if already has one)
                existing = collection.get(ids=[_doc_id(md_path)], include=["metadatas"])
                existing_summary = (existing["metadatas"][0].get("summary", "") if existing["metadatas"] else "")
                l0 = existing_summary if existing_summary else await _llm_l0(title, body)

                embed_text = f"{title}\n{body[:2000]}"
                embedding = await _embed(embed_text)

                doc_id = _doc_id(md_path)
                collection.upsert(
                    ids=[doc_id],
                    embeddings=[embedding],
                    documents=[body[:4000]],
                    metadatas=[{
                        "title": title,
                        "path": str(md_path.relative_to(VAULT_PATH)),
                        "category": category,
                        "tags": tags_str,
                        "updated": updated_iso,
                        "summary": l0,
                    }],
                )
                count += 1
            except Exception as exc:
                print(f"[indexer] skipping {md_path}: {exc}")

        index_state["last_indexed"] = datetime.now(tz=timezone.utc).isoformat()
        index_state["docs_count"] = count
        print(f"[indexer] indexed {count} documents")
    finally:
        index_state["status"] = "idle"


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------
@app.on_event("startup")
async def startup_event():
    current_count = collection.count()
    index_state["docs_count"] = current_count
    if current_count == 0:
        print("[startup] collection empty — triggering initial index")
        asyncio.create_task(_index_vault())
    else:
        print(f"[startup] collection has {current_count} docs, skipping auto-index")


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@app.get("/health")
async def health():
    return {"status": "ok", "docs_count": collection.count()}


def _keyword_boost(score: float, question: str, meta: dict, doc: str) -> float:
    """Boost score when query keywords appear in title, path, tags or body."""
    q_lower = question.lower()
    keywords = [k for k in q_lower.replace("/", " ").split() if len(k) >= 2]
    if not keywords:
        return score
    title = meta.get("title", "").lower()
    path = meta.get("path", "").lower()
    tags = meta.get("tags", "").lower()
    body = doc[:500].lower()
    boost = 0.0
    for kw in keywords:
        if kw in title:
            boost += 0.25
        elif kw in path:
            boost += 0.20
        elif kw in tags:
            boost += 0.10
        elif kw in body:
            boost += 0.05
    return min(1.0, score + boost)


def _candidates_from_keyword(question: str, where: Optional[dict]) -> dict:
    """Fetch docs where the document body contains any query keyword."""
    candidates = {}  # id -> (doc, meta, base_score)
    tokens = [t for t in question.replace("/", " ").split() if len(t) >= 2]
    variants = set()
    for t in tokens:
        variants.add(t)
        variants.add(t.upper())
        variants.add(t.capitalize())
    for variant in variants:
        try:
            kw_kwargs = {
                "where_document": {"$contains": variant},
                "include": ["documents", "metadatas"],
            }
            if where:
                kw_kwargs["where"] = where
            res = collection.get(**kw_kwargs)
            for doc_id, doc, meta in zip(res["ids"], res["documents"], res["metadatas"]):
                if doc_id not in candidates:
                    candidates[doc_id] = (doc, meta, 0.5)  # base keyword score
        except Exception:
            pass
    return candidates


@app.post("/query")
async def query(req: QueryRequest):
    if not req.question.strip():
        raise HTTPException(status_code=400, detail="question must not be empty")

    where = _build_where_clause(req.filters)
    q = req.question.strip()
    fetch_k = min(req.top_k * 4, max(collection.count(), 1))

    async def _semantic_search(query_text: str) -> dict:
        emb = await _embed(query_text)
        kwargs = {
            "query_embeddings": [emb],
            "n_results": fetch_k,
            "include": ["documents", "metadatas", "distances"],
        }
        if where:
            kwargs["where"] = where
        return collection.query(**kwargs)

    # Run main search + query expansion concurrently (expansion doesn't block main path)
    should_expand = len(q.split()) <= 3 or len(q) <= 15
    tasks = [_semantic_search(q)]
    if should_expand:
        tasks.append(_expand_query(q))

    results_gathered = await asyncio.gather(*tasks, return_exceptions=True)
    main_raw = results_gathered[0]
    expanded: list[str] = results_gathered[1] if should_expand and not isinstance(results_gathered[1], Exception) else []

    # Run expanded query searches (additive)
    extra_raws = []
    if expanded:
        extra_raws = await asyncio.gather(*[_semantic_search(eq) for eq in expanded], return_exceptions=True)

    # Build candidate pool
    pool: dict[str, tuple] = {}
    for raw in [main_raw] + [r for r in extra_raws if not isinstance(r, Exception)]:
        for doc_id, doc, meta, dist in zip(
            raw["ids"][0], raw["documents"][0],
            raw["metadatas"][0], raw["distances"][0],
        ):
            score = round(1 - dist, 4)
            if doc_id not in pool or pool[doc_id][2] < score:
                pool[doc_id] = (doc, meta, score)

    # Keyword search — add any missed keyword matches
    kw_candidates = _candidates_from_keyword(q, where)
    for doc_id, (doc, meta, kw_score) in kw_candidates.items():
        if doc_id not in pool:
            pool[doc_id] = (doc, meta, kw_score)

    # Apply keyword boost + build results
    results = []
    for doc, meta, base_score in pool.values():
        boosted = round(_keyword_boost(base_score, q, meta, doc), 4)
        # Use L0 summary as snippet; fall back to doc excerpt
        summary = meta.get("summary", "")
        snippet = summary if summary else doc[:300].replace("\n", " ").strip()
        results.append({
            "title": meta.get("title", ""),
            "path": meta.get("path", ""),
            "category": meta.get("category", ""),
            "tags": [t for t in meta.get("tags", "").split(",") if t],
            "snippet": snippet,
            "summary": summary,
            "score": boosted,
        })

    results.sort(key=lambda x: x["score"], reverse=True)
    return {"results": results[: req.top_k], "expanded_queries": expanded}


@app.get("/recent")
async def recent(hours: int = 24):
    cutoff = datetime.now(tz=timezone.utc).timestamp() - hours * 3600
    cutoff_iso = datetime.fromtimestamp(cutoff, tz=timezone.utc).isoformat()

    all_items = collection.get(include=["metadatas"])
    results = []
    for meta in all_items["metadatas"]:
        if meta.get("updated", "") >= cutoff_iso:
            results.append({
                "title": meta.get("title", ""),
                "path": meta.get("path", ""),
                "updated": meta.get("updated", ""),
                "category": meta.get("category", ""),
            })

    results.sort(key=lambda x: x["updated"], reverse=True)
    return {"results": results}


@app.post("/index")
async def trigger_index(background_tasks: BackgroundTasks):
    if index_state["status"] == "indexing":
        return {"message": "indexing already in progress"}
    background_tasks.add_task(_index_vault)
    return {"message": "indexing started"}


@app.get("/index/status")
async def index_status():
    return {
        "status": index_state["status"],
        "last_indexed": index_state["last_indexed"],
        "docs_count": collection.count(),
    }


@app.get("/audit")
async def get_audit(lines: int = 100):
    try:
        with open(AUDIT_LOG) as f2:
            all_lines = f2.readlines()
        recent = all_lines[-lines:]
        entries = []
        for line in reversed(recent):
            line = line.strip()
            if not line:
                continue
            parts = line.split()
            if len(parts) >= 4:
                entries.append({
                    "ts": parts[0],
                    "ip": parts[1],
                    "method": parts[2],
                    "path": parts[3],
                    "status": int(parts[4]) if len(parts) > 4 else 0,
                })
        return {"entries": entries, "total": len(entries)}
    except Exception as e:
        return {"entries": [], "error": str(e)}
