#!/bin/bash
# ai-switch.sh — AI tool quick-switch + rules sync
# Priority: Claude Max → Codex → GLM → Gemini → Local Ollama
#
# Usage:
#   ai-switch.sh status              Check all AI tools availability
#   ai-switch.sh codex               Switch to OpenAI Codex
#   ai-switch.sh glm                 Switch to GLM Coding Plan Pro
#   ai-switch.sh antigravity         Switch to Google Gemini (Antigravity)
#   ai-switch.sh vscode              Open VSCode + Continue.dev
#   ai-switch.sh local               Local Ollama mode (fully offline)
#
# Configuration:
#   NAS_IP        IP or hostname of your NAS (Tailscale or local)
#   NAS_OLLAMA    Full URL to Ollama API (default: http://$NAS_IP:11434)

set -euo pipefail

RULES_DIR="$HOME/.claude/rules/common"
COMBINED_RULES="/tmp/ai-rules-combined.md"
NAS_IP="${NAS_IP:-YOUR_NAS_IP}"
NAS_OLLAMA="${NAS_OLLAMA:-http://${NAS_IP}:11434}"
MCP_SERVER="${MCP_SERVER_PATH:-$HOME/scripts/kb-mcp-server.py}"

# ── Utilities ─────────────────────────────────────────────────────────────────

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'

ok()   { echo -e "${GREEN}✅ $*${NC}"; }
warn() { echo -e "${YELLOW}⚠️  $*${NC}"; }
info() { echo -e "${CYAN}   $*${NC}"; }
err()  { echo -e "${RED}❌ $*${NC}"; }

sync_rules() {
    local target="$1"
    echo "Syncing rules to $target..."
    {
        echo "# AI Coding Rules — auto-exported from Claude Code"
        echo "# $(date +%Y-%m-%d)"
        for f in "$RULES_DIR"/*.md; do
            [ -f "$f" ] && { echo -e "\n---\n"; cat "$f"; }
        done
    } > "$COMBINED_RULES"
    ok "Rules merged to $COMBINED_RULES ($(wc -l < "$COMBINED_RULES") lines)"
}

check_ollama() {
    curl -s --max-time 3 "$NAS_OLLAMA/api/tags" > /dev/null 2>&1
}

# ── status ────────────────────────────────────────────────────────────────────

cmd_status() {
    echo ""
    echo "═══════════════════════════════════════"
    echo "  AI Tools Status"
    echo "═══════════════════════════════════════"

    # Claude Code
    if claude --version > /dev/null 2>&1; then
        ok "Claude Code — $(claude --version 2>/dev/null | head -1)"
    else
        err "Claude Code — not found"
    fi

    # Codex CLI
    if codex --version > /dev/null 2>&1; then
        ok "Codex CLI — $(codex --version 2>/dev/null | head -1)"
    elif command -v codex > /dev/null 2>&1; then
        ok "Codex CLI — installed"
    else
        err "Codex CLI — not installed (npm install -g @openai/codex)"
    fi

    # Antigravity / Gemini
    if [ -d "/Applications/Antigravity.app" ]; then
        VER=$(defaults read /Applications/Antigravity.app/Contents/Info CFBundleShortVersionString 2>/dev/null || echo "?")
        ok "Antigravity v$VER (Gemini) — installed"
    else
        warn "Antigravity — not installed"
    fi

    # Cursor
    if [ -d "/Applications/Cursor.app" ]; then
        ok "Cursor — installed"
    else
        warn "Cursor — not installed"
    fi

    # Ollama (NAS)
    if check_ollama; then
        MODELS=$(curl -s "$NAS_OLLAMA/api/tags" 2>/dev/null | python3 -c "import sys,json; d=json.load(sys.stdin); print(', '.join(m['name'] for m in d.get('models',[])))" 2>/dev/null || echo "?")
        ok "Ollama NAS ($NAS_OLLAMA) — models: $MODELS"
    else
        warn "Ollama NAS — unreachable (is Tailscale/VPN connected?)"
    fi

    echo ""
}

# ── codex ─────────────────────────────────────────────────────────────────────

cmd_codex() {
    echo ""
    echo "Switching to OpenAI Codex"
    echo "─────────────────────────"

    sync_rules "Codex"

    if [ -f "$(pwd)/.git" ] || [ -d "$(pwd)/.git" ]; then
        cp "$COMBINED_RULES" "$(pwd)/AGENTS.md"
        ok "AGENTS.md written to current project"
    else
        info "Not in a git repo — rules at $COMBINED_RULES"
        info "In your project: cp $COMBINED_RULES ./AGENTS.md"
    fi

    if command -v codex > /dev/null 2>&1; then
        ok "Codex ready — run: codex"
    else
        err "Codex CLI not installed — run: npm install -g @openai/codex"
    fi
    echo ""
}

# ── glm ───────────────────────────────────────────────────────────────────────

cmd_glm() {
    echo ""
    echo "Switching to GLM Coding Plan Pro"
    echo "──────────────────────────────────"
    info "GLM runs as Claude Code plugin — all rules/agents/skills remain available"
    info "Switch model in Claude Code:"
    info "  /model glm-4"
    info "  or: export ANTHROPIC_API_KEY=<glm-api-key>"
    info ""
    info "GLM API endpoint: https://open.bigmodel.cn/api/paas/v4/"
    echo ""
}

# ── antigravity ───────────────────────────────────────────────────────────────

cmd_antigravity() {
    echo ""
    echo "Switching to Google Antigravity (Gemini)"
    echo "─────────────────────────────────────────"

    sync_rules "Antigravity"

    if [ ! -d "/Applications/Antigravity.app" ]; then
        err "Antigravity not installed"
        return 1
    fi

    info "Rules file: $COMBINED_RULES"
    info "In Antigravity: Settings → AI → Custom Instructions → point to $COMBINED_RULES"
    info ""
    info "MCP config:"
    info "  Settings → MCP → Add Server"
    info "  command: python3"
    info "  args: $MCP_SERVER"

    open -a "Antigravity"
    ok "Antigravity launched"
    echo ""
}

# ── vscode ────────────────────────────────────────────────────────────────────

cmd_vscode() {
    echo ""
    echo "Switching to VSCode + Continue.dev"
    echo "────────────────────────────────────"

    sync_rules "VSCode"

    if [ -d "$(pwd)/.git" ] || [ -f "$(pwd)/package.json" ]; then
        mkdir -p .continue
        cp "$COMBINED_RULES" .continue/rules.md
        ok ".continue/rules.md written"

        mkdir -p .vscode
        if [ ! -f ".vscode/mcp.json" ]; then
            cat > .vscode/mcp.json << EOF
{
  "servers": {
    "knowledge-base": {
      "command": "python3",
      "args": ["$MCP_SERVER"],
      "env": {
        "RAG_API_URL": "http://${NAS_IP}:8900",
        "CONTEXT_API_URL": "http://${NAS_IP}:8901",
        "KB_API_KEY": "\${KB_API_KEY}"
      }
    }
  }
}
EOF
            ok ".vscode/mcp.json created (kb-mcp-server)"
        else
            info ".vscode/mcp.json exists, skipping"
        fi
    fi

    if command -v code > /dev/null 2>&1; then
        code .
        ok "VSCode launched"
    else
        warn "VSCode CLI (code) not found — open manually"
    fi
    echo ""
}

# ── local ─────────────────────────────────────────────────────────────────────

cmd_local() {
    echo ""
    echo "Switching to Local Ollama Mode (fully offline)"
    echo "────────────────────────────────────────────────"

    if check_ollama; then
        ok "Ollama NAS reachable ($NAS_OLLAMA)"
        MODELS=$(curl -s "$NAS_OLLAMA/api/tags" 2>/dev/null | python3 -c "import sys,json; d=json.load(sys.stdin); print(', '.join(m['name'] for m in d.get('models',[])))" 2>/dev/null || echo "unknown")
        info "Available models: $MODELS"
    else
        warn "Ollama NAS unreachable — check VPN/Tailscale"
        info "Connect: tailscale up"
    fi

    info ""
    info "In VSCode Continue.dev:"
    info "  Provider: Ollama"
    info "  Base URL: $NAS_OLLAMA"
    info "  Recommended: qwen2.5:7b-instruct-q4_K_M"

    if command -v code > /dev/null 2>&1; then
        code .
        ok "VSCode launched"
    fi
    echo ""
}

# ── Entry point ───────────────────────────────────────────────────────────────

case "${1:-help}" in
    status)      cmd_status ;;
    codex)       cmd_codex ;;
    glm)         cmd_glm ;;
    antigravity) cmd_antigravity ;;
    vscode)      cmd_vscode ;;
    local)       cmd_local ;;
    *)
        echo ""
        echo "Usage: ai-switch.sh <command>"
        echo ""
        echo "Commands:"
        echo "  status       Check all AI tool availability"
        echo "  codex        Switch to OpenAI Codex"
        echo "  glm          Switch to GLM Coding Plan Pro"
        echo "  antigravity  Switch to Google Antigravity (Gemini)"
        echo "  vscode       Open VSCode + Continue.dev"
        echo "  local        Local Ollama mode (fully offline)"
        echo ""
        echo "Priority: Claude → Codex → GLM → Gemini → Local Ollama"
        echo ""
        ;;
esac
