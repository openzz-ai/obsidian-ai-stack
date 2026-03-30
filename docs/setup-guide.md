# Setup Guide

## Prerequisites

| Component | Minimum | Recommended |
|-----------|---------|-------------|
| Server OS | Any Linux | Unraid / TrueNAS / Debian |
| RAM | 4 GB | 8 GB+ |
| Storage | 10 GB free | SSD for ChromaDB |
| GPU | None (CPU fallback) | NVIDIA (Tesla P4 / RTX) |
| Docker | 24+ | latest |
| Python | 3.10+ (Mac) | 3.11 |

---

## Step 1: Install Ollama on your server

```bash
curl -fsSL https://ollama.com/install.sh | sh

# Pull required models
ollama pull nomic-embed-text    # embedding model (~274 MB)
ollama pull qwen2.5:7b-instruct-q4_K_M   # chat model for summaries (~4.7 GB)

# Verify
ollama list
```

---

## Step 2: Configure SSH access from your Mac

```bash
# ~/.ssh/config on your Mac
Host nas
    HostName your-nas-ip-or-hostname
    User root
    Port 22
    IdentityFile ~/.ssh/id_ed25519
    ServerAliveInterval 30

# Test
ssh nas "echo ok"
```

---

## Step 3: Deploy RAG service on server

```bash
# On your server
git clone https://github.com/YOUR_USERNAME/obsidian-ai-stack.git
cd obsidian-ai-stack/rag-service

# Configure
cp .env.example .env
# Edit .env — set KB_API_KEY and VAULT_PATH

# Start
docker compose up -d

# Check health
curl http://localhost:8900/health
# → {"status":"ok","docs_count":0}
```

---

## Step 4: Set up vault sync on Mac

```bash
# Edit scripts/vault-sync.sh
# Change SOURCE to your Obsidian vault path
# Change NAS_SSH_ALIAS if you used a different name in ~/.ssh/config

# Make executable
chmod +x scripts/vault-sync.sh

# Add API key and test manually
RAG_API_KEY=your-api-key ./scripts/vault-sync.sh

# Add to crontab (every 10 minutes, inherits user permissions)
crontab -e
# Add this line:
# */10 * * * * RAG_API_KEY=your-api-key /path/to/vault-sync.sh >> /tmp/vault-sync.log 2>&1
```

**Important on macOS**: Use `crontab`, not `LaunchAgent`. macOS TCC (Transparency, Consent, Control) blocks LaunchAgent processes from reading `~/Documents` even with Full Disk Access granted in System Settings. Crontab runs as your user and inherits FDA permissions.

---

## Step 5: Trigger initial index

```bash
curl -X POST http://YOUR_NAS_IP:8900/index \
  -H "X-API-Key: your-api-key"

# Monitor progress
curl http://YOUR_NAS_IP:8900/index/status \
  -H "X-API-Key: your-api-key"
```

L0 summary generation is the slow part (~2-4s per document for first index). Subsequent reindexes skip documents that already have summaries.

---

## Step 6: Configure MCP server on Mac

```bash
# Install dependencies
pip install fastmcp httpx

# Add to Claude Code config (~/.claude/claude_desktop_config.json):
{
  "mcpServers": {
    "knowledge-base": {
      "command": "python3",
      "args": ["/path/to/scripts/kb-mcp-server.py"],
      "env": {
        "RAG_API_URL": "http://YOUR_NAS_IP:8900",
        "CONTEXT_API_URL": "http://YOUR_NAS_IP:8901",
        "KB_API_KEY": "your-api-key"
      }
    }
  }
}
```

Restart Claude Code. The tools `kb_search`, `kb_recent`, `context_read`, `context_write`, `radar_today` should appear.

---

## Step 7 (Optional): Hub Dashboard

```bash
# Copy dashboard to server
scp dashboard/hub-dashboard.html nas:/mnt/user/appdata/hub-dashboard/

# Serve with Nginx or caddy
# The dashboard expects these endpoints on the same origin:
#   GET  /api/status   → service health + metrics
#   GET  /api/feed     → agent activity feed
#   GET  /api/audit    → RAG HTTP audit log
#   GET  /api/search   → RAG search (proxied from /query)
```

---

## Troubleshooting

### rsync exit 23 on Mac
TCC is blocking the LaunchAgent. Switch to crontab (see Step 4).

### "unauthorized" from /index
Missing or wrong `X-API-Key` header. Check `KB_API_KEY` is set in `.env` and matches the header you're sending.

### Search returns irrelevant results for proper nouns
The keyword boost should handle this. If not:
- Check that the query word appears in the document title/path/tags
- Verify `_derive_category()` maps your folder structure correctly

### Ollama not reachable from container
Add `extra_hosts: ["host.docker.internal:host-gateway"]` to `docker-compose.yml` (already included in the template).

### MCP server not connecting
The server auto-creates an SSH tunnel as a last resort. Ensure the `nas` SSH alias is configured and `ssh nas "echo ok"` works without a password prompt.
