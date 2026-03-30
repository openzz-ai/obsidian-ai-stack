#!/usr/bin/env python3
"""Obsidian Vault Frontmatter 标准化工具

批量为 Obsidian Vault 中的 .md 文件添加/补全标准化 frontmatter。
保留已有值，只补充缺失字段，tags 取并集。

Usage:
    python3 vault-frontmatter.py --dry-run           # 预览变更
    python3 vault-frontmatter.py --dry-run --verbose  # 逐文件差异
    python3 vault-frontmatter.py --dir EX2026         # 只处理指定目录
    python3 vault-frontmatter.py --report             # 覆盖率报告
    python3 vault-frontmatter.py                      # 正式执行
"""

import argparse
import os
import re
import shutil
from datetime import datetime
from pathlib import Path

import yaml

# ── 配置 ─────────────────────────────────────────────

VAULT_ROOT = Path("/Users/zhanghui/Documents/mrzob/mrz")
BACKUP_DIR = VAULT_ROOT / ".backups" / datetime.now().strftime("%Y-%m-%d")

EXCLUDE_DIRS = {".obsidian", ".claude", ".backups"}
EXCLUDE_FILES = {"CLAUDE.md", "TASKS.md"}

# 目录 → category 映射（最长前缀匹配）
DIR_CATEGORY_MAP = {
    "EX2026/memory":                    "memory",
    "EX2026":                           "strategy",
    "全球雷达/日报":                      "radar",
    "全球雷达/周报":                      "radar",
    "全球雷达":                           "radar",
    "技术笔记/OpenClaw/Prompts":         "prompt",
    "技术笔记/OpenClaw/claude-cowork/Prompts": "prompt",
    "技术笔记/OpenClaw/claude-cowork/Skills":  "skill",
    "技术笔记/openclaw-skills":          "skill",
    "技术笔记/OpenClaw/配置参考":         "tech-note",
    "技术笔记/OpenClaw":                 "tech-note",
    "技术笔记":                           "tech-note",
    "报告":                              "report",
    "exsop":                             "report",
    "cowork":                            "tech-note",
}

# 目录 → 默认 tags 前缀
DIR_TAGS_MAP = {
    "EX2026/memory":                    ["exrobots/memory"],
    "EX2026":                           ["exrobots/strategy"],
    "全球雷达/日报":                      ["radar/daily"],
    "全球雷达/周报":                      ["radar/weekly"],
    "全球雷达":                           ["radar"],
    "技术笔记/OpenClaw/Prompts":         ["openclaw/prompt"],
    "技术笔记/OpenClaw/claude-cowork":   ["openclaw/cowork"],
    "技术笔记/OpenClaw/配置参考":         ["openclaw/config"],
    "技术笔记/OpenClaw/飞书(Lark)":      ["openclaw/feishu"],
    "技术笔记/OpenClaw/Twitter-X":       ["openclaw/twitter"],
    "技术笔记/OpenClaw/memory-lancedb-pro": ["openclaw/memory"],
    "技术笔记/OpenClaw/Skills知识库":     ["openclaw/skill"],
    "技术笔记/OpenClaw/agent团队":       ["openclaw/agent"],
    "技术笔记/OpenClaw":                 ["openclaw"],
    "技术笔记/openclaw-skills":          ["openclaw/skill"],
    "技术笔记/NAS环境":                   ["infra/nas"],
    "技术笔记/Claude Code":              ["ai/claude"],
    "技术笔记/智能代理(Agent)":           ["ai/agent"],
    "技术笔记/hesen":                    ["tool"],
    "技术笔记/软件开发项":                ["tool"],
    "技术笔记":                           [],
    "报告":                              ["exrobots"],
    "cowork":                            ["tool/automation"],
}

# status 标准化映射
STATUS_NORMALIZE = {
    "已解决": "archive",
    "已验证可用": "archive",
    "已配置": "archive",
    "已记录": "archive",
    "已完成": "archive",
    "进行中": "active",
    "复盘完成": "reference",
    "调研完成": "reference",
}

# category → 默认 status
CATEGORY_DEFAULT_STATUS = {
    "strategy": "active",
    "radar": "reference",
    "tech-note": "reference",
    "report": "reference",
    "memory": "reference",
    "skill": "reference",
    "prompt": "reference",
    "deploy": "reference",
}

# 日期提取正则
DATE_PATTERN = re.compile(r"(\d{4}-\d{2}-\d{2})")
INLINE_DATE_PATTERN = re.compile(
    r"(?:日期|创建时间|记录时间|时间|date)[：:\s]*(\d{4}-\d{2}-\d{2})", re.IGNORECASE
)


# ── 解析 ─────────────────────────────────────────────

def parse_frontmatter(content: str) -> tuple[dict | None, str]:
    """解析 --- 分隔的 YAML frontmatter。

    仅当文件首行为 '---' 时才识别为 frontmatter。
    返回 (parsed_dict, body) 或 (None, full_content)。
    """
    lines = content.split("\n")
    if not lines or lines[0].strip() != "---":
        return None, content

    end_idx = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end_idx = i
            break

    if end_idx is None:
        return None, content

    yaml_block = "\n".join(lines[1:end_idx])
    body = "\n".join(lines[end_idx + 1:])

    try:
        fm = yaml.safe_load(yaml_block)
        if not isinstance(fm, dict):
            return None, content
        return fm, body
    except yaml.YAMLError:
        return None, content


def generate_frontmatter(metadata: dict) -> str:
    """生成标准化 YAML frontmatter 字符串。"""
    # 字段排序
    field_order = ["tags", "date", "updated", "status", "category", "type"]
    ordered = {}
    for key in field_order:
        if key in metadata:
            ordered[key] = metadata[key]
    # 保留其他字段（如 skills, agentId, env 等）
    for key in metadata:
        if key not in ordered:
            ordered[key] = metadata[key]

    lines = ["---"]
    for key, value in ordered.items():
        if key == "tags" and isinstance(value, list):
            lines.append("tags:")
            for tag in value:
                lines.append(f"  - {tag}")
        elif isinstance(value, list):
            lines.append(f"{key}:")
            for item in value:
                lines.append(f"  - {item}")
        elif isinstance(value, dict):
            # 保留复杂结构（如 env）原样
            dumped = yaml.dump({key: value}, default_flow_style=False, allow_unicode=True).strip()
            lines.append(dumped)
        else:
            lines.append(f"{key}: {value}")
    lines.append("---")
    return "\n".join(lines)


# ── 推断元数据 ───────────────────────────────────────

def get_relative_dir(filepath: Path) -> str:
    """获取相对于 VAULT_ROOT 的目录路径。"""
    try:
        return str(filepath.parent.relative_to(VAULT_ROOT))
    except ValueError:
        return ""


def match_dir_prefix(reldir: str, mapping: dict) -> str | list | None:
    """最长前缀匹配目录映射。"""
    best_match = None
    best_len = -1
    for prefix, value in mapping.items():
        if reldir == prefix or reldir.startswith(prefix + "/"):
            if len(prefix) > best_len:
                best_match = value
                best_len = len(prefix)
        elif prefix == "." and reldir == "":
            if best_len < 0:
                best_match = value
                best_len = 0
    return best_match


def infer_type(filepath: Path, body: str) -> str:
    """根据文件名和内容推断 type。"""
    name = filepath.stem

    if "MOC" in name or "总览" in name:
        return "moc"
    if name.startswith("daily-report-"):
        return "daily-report"
    if re.match(r"^\d{4}-\d{2}-\d{2}$", name):
        return "daily-log"
    if name.startswith("weekly-") or "周报" in name:
        return "weekly-report"
    if "monitor" in name.lower() or "监控" in name:
        return "monitor"
    if name == "SKILL":
        return "skill"
    if "系统提示词" in name or "Prompt" in name.lower():
        return "prompt"
    if "索引" in name or "Index" in name.lower():
        return "index"

    reldir = get_relative_dir(filepath)
    cat = match_dir_prefix(reldir, DIR_CATEGORY_MAP)
    if cat == "strategy":
        return "business-plan"
    if cat in ("skill", "prompt", "deploy"):
        return cat
    return "tech-doc"


def infer_date(filepath: Path, existing_fm: dict | None, body: str) -> str | None:
    """推断日期：已有值 > 文件名 > 内容中的日期 > mtime。"""
    if existing_fm and existing_fm.get("date"):
        d = existing_fm["date"]
        if isinstance(d, datetime):
            return d.strftime("%Y-%m-%d")
        return str(d)

    # 文件名中提取日期
    m = DATE_PATTERN.search(filepath.stem)
    if m:
        return m.group(1)

    # 内容前 10 行中提取
    first_lines = "\n".join(body.split("\n")[:10])
    m = INLINE_DATE_PATTERN.search(first_lines)
    if m:
        return m.group(1)

    # fallback: mtime
    mtime = filepath.stat().st_mtime
    return datetime.fromtimestamp(mtime).strftime("%Y-%m-%d")


def infer_updated(filepath: Path) -> str:
    """文件 mtime 作为 updated。"""
    mtime = filepath.stat().st_mtime
    return datetime.fromtimestamp(mtime).strftime("%Y-%m-%d")


def infer_status(existing_fm: dict | None, category: str) -> str:
    """推断 status。"""
    if existing_fm and existing_fm.get("status"):
        raw = str(existing_fm["status"])
        return STATUS_NORMALIZE.get(raw, raw)
    return CATEGORY_DEFAULT_STATUS.get(category, "reference")


def infer_tags(filepath: Path, existing_fm: dict | None) -> list[str]:
    """合并已有 tags 和目录推断的 tags。"""
    existing_tags = []
    if existing_fm and existing_fm.get("tags"):
        raw = existing_fm["tags"]
        if isinstance(raw, list):
            existing_tags = [str(t) for t in raw]
        elif isinstance(raw, str):
            existing_tags = [t.strip() for t in raw.split(",")]

    reldir = get_relative_dir(filepath)
    dir_tags = match_dir_prefix(reldir, DIR_TAGS_MAP)
    if dir_tags is None:
        dir_tags = []

    # 合并去重，保持顺序（已有在前）
    seen = set()
    merged = []
    for tag in existing_tags + dir_tags:
        if tag not in seen:
            seen.add(tag)
            merged.append(tag)
    return merged


def infer_metadata(filepath: Path, existing_fm: dict | None, body: str) -> dict:
    """推断完整的标准化元数据。"""
    reldir = get_relative_dir(filepath)
    category = match_dir_prefix(reldir, DIR_CATEGORY_MAP) or "tech-note"

    return {
        "tags": infer_tags(filepath, existing_fm),
        "date": infer_date(filepath, existing_fm, body),
        "updated": infer_updated(filepath),
        "status": infer_status(existing_fm, category),
        "category": category,
        "type": infer_type(filepath, body),
    }


def merge_frontmatter(existing: dict | None, inferred: dict) -> dict:
    """合并已有 frontmatter 和推断值。已有值优先。"""
    merged = dict(inferred)

    if existing:
        # 保留已有字段的值（tags 特殊处理）
        for key, value in existing.items():
            if key == "tags":
                continue  # tags 已在 infer_tags 中合并
            if key in ("date", "updated", "status"):
                if value is not None and str(value).strip():
                    if key == "status":
                        merged[key] = STATUS_NORMALIZE.get(str(value), str(value))
                    elif key == "date" and isinstance(value, datetime):
                        merged[key] = value.strftime("%Y-%m-%d")
                    else:
                        merged[key] = value
            elif key not in merged:
                # 保留扩展字段 (skills, agentId, env 等)
                merged[key] = value

    return merged


# ── 处理单个文件 ─────────────────────────────────────

def process_file(filepath: Path, dry_run: bool, verbose: bool) -> dict:
    """处理单个文件，返回操作信息。"""
    try:
        content = filepath.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        relpath = filepath.relative_to(VAULT_ROOT)
        if verbose:
            print(f"\n  ⚠️  跳过 (编码错误): {relpath}")
        return {
            "path": relpath,
            "action": "skip",
            "category": "",
            "type": "",
            "had_fm": False,
        }
    existing_fm, body = parse_frontmatter(content)

    inferred = infer_metadata(filepath, existing_fm, body)
    merged = merge_frontmatter(existing_fm, inferred)

    new_fm_str = generate_frontmatter(merged)
    new_content = new_fm_str + "\n" + body.lstrip("\n")

    # 判断是否有变化
    action = "skip"
    if existing_fm is None:
        action = "add"
    else:
        # 检查是否有新字段
        new_fields = set(merged.keys()) - set(existing_fm.keys())
        changed_tags = set(merged.get("tags", [])) != set(
            existing_fm.get("tags", []) if isinstance(existing_fm.get("tags"), list) else []
        )
        if new_fields or changed_tags:
            action = "update"

    relpath = filepath.relative_to(VAULT_ROOT)

    if verbose and action != "skip":
        print(f"\n{'='*60}")
        print(f"  {action.upper()}: {relpath}")
        print(f"{'='*60}")
        if existing_fm:
            print(f"  现有字段: {list(existing_fm.keys())}")
        print(f"  新 frontmatter:")
        for line in new_fm_str.split("\n"):
            print(f"    {line}")

    if not dry_run and action != "skip":
        # 备份
        backup_path = BACKUP_DIR / relpath
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(filepath, backup_path)
        # 写入
        filepath.write_text(new_content, encoding="utf-8")

    return {
        "path": relpath,
        "action": action,
        "category": merged.get("category", ""),
        "type": merged.get("type", ""),
        "had_fm": existing_fm is not None,
    }


# ── 扫描 Vault ──────────────────────────────────────

def scan_vault(target_dir: str | None = None) -> list[Path]:
    """递归扫描 Vault 中的 .md 文件。"""
    root = VAULT_ROOT
    if target_dir:
        root = VAULT_ROOT / target_dir
        if not root.exists():
            print(f"错误: 目录不存在 {root}")
            return []

    files = []
    for filepath in sorted(root.rglob("*.md")):
        # 排除目录
        relpath = filepath.relative_to(VAULT_ROOT)
        parts = relpath.parts
        if any(p in EXCLUDE_DIRS for p in parts):
            continue
        # 排除 cowork/newstart 下的脚本
        if "cowork" in parts and "newstart" in parts:
            continue
        # 排除特定文件
        if filepath.name in EXCLUDE_FILES:
            continue
        files.append(filepath)
    return files


# ── 报告 ─────────────────────────────────────────────

def print_report(results: list[dict]):
    """输出覆盖率报告。"""
    total = len(results)
    had_fm = sum(1 for r in results if r["had_fm"])
    added = sum(1 for r in results if r["action"] == "add")
    updated = sum(1 for r in results if r["action"] == "update")
    skipped = sum(1 for r in results if r["action"] == "skip")

    print(f"\n{'='*60}")
    print(f"  Obsidian Vault Frontmatter 报告")
    print(f"{'='*60}")
    print(f"  总文件数:     {total}")
    print(f"  已有 FM:      {had_fm} ({had_fm*100//total if total else 0}%)")
    print(f"  新增 FM:      {added}")
    print(f"  更新 FM:      {updated}")
    print(f"  无变化:       {skipped}")
    print(f"  处理后覆盖率: 100%")

    # 按目录统计
    dir_stats: dict[str, dict] = {}
    for r in results:
        d = str(r["path"].parent) if str(r["path"].parent) != "." else "(root)"
        if d not in dir_stats:
            dir_stats[d] = {"total": 0, "add": 0, "update": 0, "skip": 0}
        dir_stats[d]["total"] += 1
        dir_stats[d][r["action"]] += 1

    print(f"\n  按目录:")
    print(f"  {'目录':<45} {'总数':>4} {'新增':>4} {'更新':>4} {'跳过':>4}")
    print(f"  {'-'*65}")
    for d in sorted(dir_stats.keys()):
        s = dir_stats[d]
        print(f"  {d:<45} {s['total']:>4} {s['add']:>4} {s['update']:>4} {s['skip']:>4}")

    # 按 category 统计
    cat_stats: dict[str, int] = {}
    for r in results:
        cat = r["category"]
        cat_stats[cat] = cat_stats.get(cat, 0) + 1

    print(f"\n  按 Category:")
    for cat in sorted(cat_stats.keys()):
        print(f"    {cat}: {cat_stats[cat]}")

    # 按 type 统计
    type_stats: dict[str, int] = {}
    for r in results:
        t = r["type"]
        type_stats[t] = type_stats.get(t, 0) + 1

    print(f"\n  按 Type:")
    for t in sorted(type_stats.keys()):
        print(f"    {t}: {type_stats[t]}")
    print()


# ── 主入口 ───────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Obsidian Vault Frontmatter 标准化")
    parser.add_argument("--dry-run", action="store_true", help="预览模式，不修改文件")
    parser.add_argument("--verbose", action="store_true", help="显示每个文件的详细变更")
    parser.add_argument("--dir", type=str, help="只处理指定子目录")
    parser.add_argument("--report", action="store_true", help="只输出覆盖率报告（不修改）")
    parser.add_argument("--yes", "-y", action="store_true", help="无人值守模式，跳过确认")
    args = parser.parse_args()

    if args.report:
        args.dry_run = True

    files = scan_vault(args.dir)
    if not files:
        print("未找到任何 .md 文件")
        return

    print(f"扫描到 {len(files)} 个 .md 文件")

    if not args.dry_run:
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        print(f"备份目录: {BACKUP_DIR}")
        if not args.yes:
            confirm = input(f"\n确认对 {len(files)} 个文件执行 frontmatter 标准化？[y/N] ")
            if confirm.lower() != "y":
                print("已取消")
                return

    results = []
    for filepath in files:
        result = process_file(filepath, args.dry_run, args.verbose)
        results.append(result)

    print_report(results)

    if args.dry_run and not args.report:
        print("  [DRY RUN] 未修改任何文件。去掉 --dry-run 正式执行。\n")
    elif not args.dry_run:
        added = sum(1 for r in results if r["action"] == "add")
        updated = sum(1 for r in results if r["action"] == "update")
        print(f"  ✅ 完成！新增 {added} 个，更新 {updated} 个。备份在 {BACKUP_DIR}\n")


if __name__ == "__main__":
    main()
