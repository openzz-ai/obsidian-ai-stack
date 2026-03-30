# obsidian-ai-stack

[English](#obsidian-ai-stack) | [中文](#中文介绍)

**Self-hosted AI knowledge base pipeline built around Obsidian — runs entirely on local hardware (NAS + GPU), zero cloud cost.**

[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.11+-blue.svg)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.110+-green.svg)](https://fastapi.tiangolo.com)

---

## Architecture

```
MacBook (dev) ──rsync──▶ NAS / Home Server (GPU)
                              ├── Ollama :11434   Qwen2.5-7B + nomic-embed-text
                              ├── RAG Service :8900   FastAPI + ChromaDB
                              ├── Context API :8901   cross-agent shared memory
                              └── Hub Dashboard :8085
                                       ↑
                              Claude Code (MCP) ──kb-mcp-server──┘
```

The Obsidian vault on your Mac is synced to the server via `rsync + crontab`. The RAG service indexes all Markdown files into ChromaDB, with Ollama providing local embeddings (no API cost). Claude Code connects via a FastMCP server and can search, read context, and write notes during conversations.

---

## What's Inside

| Path | Description |
|------|-------------|
| `rag-service/app.py` | FastAPI RAG service — hybrid search, L0 summaries, query expansion |
| `rag-service/docker-compose.yml` | One-command NAS deployment template |
| `scripts/vault-sync.sh` | Obsidian vault → NAS sync via rsync, auto-triggers reindex |
| `scripts/kb-mcp-server.py` | Claude Code MCP server: `kb_search`, `context_read/write`, `radar_today` |
| `scripts/ai-switch.sh` | Switch between Claude / Codex / GLM / Gemini / local Ollama |
| `scripts/vault-frontmatter.py` | Batch-standardize Obsidian frontmatter (category, tags, status) |
| `scripts/claude-config-backup.sh` | Backup Claude Code config to remote server |
| `dashboard/hub-dashboard.html` | Alpine.js monitoring dashboard: RAG search, audit log, agent feed |

---

## Key Features

- **Hybrid Search** — semantic vectors (ChromaDB cosine) + keyword recall (`$contains`) + score boosting by title / path / tags / body
- **L0 Summaries** — Qwen2.5-7B generates one-sentence summaries at index time, stored in ChromaDB metadata. Used as search snippets instead of raw text truncation.
- **Query Expansion** — short/ambiguous queries are expanded into 2 semantic variants via LLM, running **concurrently** with the main search to avoid added latency (~1.3s total)
- **Cross-Agent Context** — persistent SQLite-backed context API lets multiple Claude sessions share memory and decisions
- **Zero Cloud Cost** — all inference (embedding + chat) runs on local Ollama; no OpenAI/API spend required
- **Obsidian-Native** — categories derived automatically from vault folder structure; no manual frontmatter needed
- **API Key Auth** — `X-API-Key` middleware with full audit logging on all endpoints
- **Multi-Endpoint Failover** — MCP server tries primary → fallback → SSH tunnel auto-created on connection failure

---

## Stack

```
Backend    FastAPI · ChromaDB · Ollama · nomic-embed-text · Qwen2.5-7B · httpx · FastMCP
Frontend   Alpine.js · Tailwind CSS (CDN) · JetBrains Mono
Infra      Docker (Unraid/NAS) · rsync · crontab · Tailscale · SSH tunneling
```

---

## Quick Start

### 1. NAS / Server side

```bash
# Clone and configure
git clone https://github.com/YOUR_USERNAME/obsidian-ai-stack.git
cd obsidian-ai-stack/rag-service

# Copy and edit the env file
cp .env.example .env
# Edit .env: set KB_API_KEY, VAULT_PATH, OLLAMA_CHAT_MODEL

# Start the RAG service
docker compose up -d

# Verify
curl http://localhost:8900/health
```

### 2. Mac side — vault sync

```bash
# Edit SOURCE and NAS_SSH_ALIAS in vault-sync.sh
# Then add to crontab (every 10 minutes):
crontab -e
# Add: */10 * * * * RAG_API_KEY=your-key /path/to/vault-sync.sh >> /tmp/vault-sync.log 2>&1
```

**Why crontab, not LaunchAgent?** On macOS, LaunchAgent processes are blocked by TCC (Transparency, Consent, Control) from accessing `~/Documents`. Crontab inherits the user's Full Disk Access.

### 3. Claude Code — MCP server

Add to `~/.claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "knowledge-base": {
      "command": "python3",
      "args": ["/path/to/scripts/kb-mcp-server.py"],
      "env": {
        "RAG_API_URL": "http://YOUR_NAS_IP:8900",
        "CONTEXT_API_URL": "http://YOUR_NAS_IP:8901",
        "KB_API_KEY": "your-api-key-here"
      }
    }
  }
}
```

Then in Claude Code, `kb_search`, `context_read`, `context_write`, and `radar_today` tools become available.

### 4. Hub Dashboard

Copy `dashboard/hub-dashboard.html` to your NAS and serve it via a simple static server or Nginx. The dashboard reads from `/api/status`, `/api/feed`, `/api/audit`, and `/api/search` — wire these endpoints to your RAG service.

---

## Configuration Reference

### RAG Service (docker-compose env)

| Variable | Default | Description |
|----------|---------|-------------|
| `VAULT_PATH` | `/data/vault` | Path to your Obsidian vault inside the container |
| `CHROMADB_PATH` | `/data/chromadb` | ChromaDB persistence directory |
| `OLLAMA_URL` | `http://host.docker.internal:11434` | Ollama API URL |
| `OLLAMA_CHAT_MODEL` | `qwen2.5:7b-instruct-q4_K_M` | LLM for summaries and query expansion |
| `KB_API_KEY` | _(empty = no auth)_ | API key for `X-API-Key` authentication |
| `SKIP_DIRS_EXTRA` | _(empty)_ | Comma-separated directories to exclude from indexing |

### MCP Server (env vars)

| Variable | Description |
|----------|-------------|
| `RAG_API_URL` | Primary RAG service URL |
| `CONTEXT_API_URL` | Primary Context API URL |
| `KB_API_KEY` | API key passed as `X-API-Key` header |
| `NAS_SSH_ALIAS` | SSH alias for tunnel fallback (default: `nas`) |

---

## Customization

**Vault folder → category mapping** — edit `_derive_category()` in `rag-service/app.py` to match your Obsidian structure:

```python
def _derive_category(rel_path: Path) -> str:
    top = rel_path.parts[0]
    mapping = {
        "Projects":   "project",
        "Literature": "literature",
        "Daily":      "daily",
        "Archive":    "archive",
    }
    return mapping.get(top, "note")
```

**Query expansion domain context** — edit the prompt in `_expand_query()` to describe your knowledge base domain for better expansion accuracy.

**Dashboard category chips** — edit the `cat` array in `hub-dashboard.html` to match your category names.

---

## Hardware Notes

This stack was developed on:
- **Dev machine**: MacBook M4
- **NAS**: Unraid with Tesla P4 8GB GPU (Ollama inference)
- **Network**: Tailscale mesh VPN for remote access + SSH tunnels as fallback

The RAG service runs fine on CPU — GPU (even an old Tesla P4) primarily speeds up LLM inference for L0 summary generation and query expansion.

---

## Related Projects

This project was inspired by / borrows concepts from:

- [volcengine/OpenViking](https://github.com/volcengine/OpenViking) — L0/L1 hierarchical retrieval concept
- [ObsidianRAG](https://github.com/Vasallo94/ObsidianRAG) — Obsidian + Ollama RAG
- [ChromaDB](https://github.com/chroma-core/chroma) — vector database
- [FastMCP](https://github.com/jlowin/fastmcp) — MCP server framework

---

## License

MIT — see [LICENSE](LICENSE).

---

---

[↑ Back to English](#obsidian-ai-stack)

## 中文介绍

**个人 AI 知识库全链路，基于 Obsidian + 自托管 NAS GPU 推理，零云端费用。**

### 架构

```
MacBook 写笔记
    ↓ rsync + crontab（每10分钟）
NAS 服务器（Tesla P4 GPU）
    ├── Ollama  本地 LLM 推理（Qwen2.5-7B + nomic-embed-text）
    ├── RAG 服务  FastAPI + ChromaDB 向量检索
    ├── Context API  跨 Agent 共享上下文（SQLite）
    └── Hub Dashboard  监控面板
             ↑
Claude Code（MCP 协议）──────────────────────┘
```

### 核心能力

| 特性 | 说明 |
|------|------|
| **混合检索** | 语义向量 + 关键词召回 + 标题/路径/标签权重加成，解决专有名词搜索不准的问题 |
| **L0 摘要** | 索引时 Qwen2.5-7B 生成一句话摘要，存入 ChromaDB metadata，搜索结果直接展示 |
| **查询扩展** | 短查询并发扩展为2个语义变体，与主搜索同步执行，不增加延迟（总 ~1.3s） |
| **跨 Agent 上下文** | Context API 让多个 Claude 会话共享决策记忆 |
| **全本地推理** | Ollama + 本地 GPU，无 API 费用 |
| **Obsidian 原生** | 从文件夹路径自动推导 category，无需手动填 frontmatter |
| **API Key 鉴权** | 全端点 X-API-Key 认证 + 审计日志 |
| **多端点容灾** | MCP Server 按优先级尝试：主 IP → 备用 IP → SSH 隧道（自动建立） |

### 适合人群

- Obsidian 重度用户，笔记量超过 50 篇
- 拥有 NAS 或家庭服务器（有 GPU 更佳，无 GPU 也可运行）
- Claude Code / Codex / Gemini CLI 用户，希望 AI 能搜索自己的知识库
- 不想将私人笔记发送到云端 API

### 快速部署

参见上方 [Quick Start](#quick-start) 章节（中英文步骤一致）。

**macOS 同步注意**：必须用 crontab，不能用 LaunchAgent — macOS TCC 隐私机制会阻止 LaunchAgent 读取 `~/Documents`，而 crontab 继承用户的完整磁盘访问权限。
