#!/usr/bin/env python3
"""Mirror Trellis Claude harness into official Grok Build layout (#433).

Writes:
  .grok/skills/trellis-*/
  .grok/agents/trellis-*.md   (with pull-based prelude)
  .grok/commands/trellis-*.md (flat)

Does NOT write hooks (Grok is class-2 pull-based).

Usage:
  python migrate-trellis-to-grok.py           # dry-run
  python migrate-trellis-to-grok.py --apply   # write
"""
from __future__ import annotations

import argparse
import re
import shutil
from pathlib import Path

PULL_PRELUDE = """
## Dispatch note (Grok Build)

Grok does **not** auto-inject SessionStart / UserPromptSubmit task context.
Always pull context yourself before acting:

- `.trellis/workflow.md`
- `.trellis/spec/`
- Active task `prd.md`
- `design.md` / `implement.md` if present
- Task `implement.jsonl` / `check.jsonl` if present

Main session should dispatch with:

```text
spawn_subagent(
  subagent_type="trellis-implement",  # or trellis-check / trellis-research
  prompt="Active task: <task-path>\\n..."
)
```
""".strip()


def rewrite(text: str) -> str:
    text = text.replace("/trellis:", "/trellis-")
    text = text.replace(".claude/skills/", ".grok/skills/")
    text = text.replace(".claude/agents/", ".grok/agents/")
    text = text.replace(".claude/commands/", ".grok/commands/")
    return text


def normalize_markdown(text: str) -> str:
    """Keep generated Markdown diff-clean without changing its content."""
    return "\n".join(line.rstrip() for line in text.rstrip().splitlines()) + "\n"


def ensure_pull_prelude(text: str, agent_name: str) -> str:
    """Inject pull prelude into implement/check agents if missing."""
    if agent_name not in {"trellis-implement", "trellis-check"}:
        return text
    if "does **not** auto-inject" in text or "does not auto-inject" in text:
        return text
    # insert after first H1 or after frontmatter
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            body = parts[2].lstrip("\n")
            return f"---{parts[1]}---\n\n{PULL_PRELUDE}\n\n{body}"
    return PULL_PRELUDE + "\n\n" + text


def copy_dir(src: Path, dst: Path, apply: bool) -> None:
    print(f"  DIR  {src.as_posix()} -> {dst.as_posix()}")
    if not apply:
        return
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst)
    for markdown_path in dst.rglob("*.md"):
        markdown_path.write_text(
            normalize_markdown(markdown_path.read_text(encoding="utf-8")),
            encoding="utf-8",
            newline="\n",
        )


def write_text(path: Path, content: str, apply: bool) -> None:
    print(f"  FILE {path.as_posix()}")
    if not apply:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(normalize_markdown(content), encoding="utf-8", newline="\n")


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Mirror Trellis Claude files into .grok/ (skills/agents/commands, no hooks)"
    )
    ap.add_argument("--root", default=".", help="project root (default: cwd)")
    ap.add_argument("--apply", action="store_true", help="write files (default: dry-run)")
    args = ap.parse_args()

    root = Path(args.root).resolve()
    claude = root / ".claude"
    grok = root / ".grok"

    if not claude.is_dir():
        print(f"[error] missing {claude}")
        return 1

    print(f"root: {root}")
    print(f"mode: {'APPLY' if args.apply else 'DRY-RUN'}\n")

    # 1) skills
    print("## skills")
    n = 0
    skills_src = claude / "skills"
    if skills_src.is_dir():
        for d in sorted(skills_src.iterdir()):
            if d.is_dir() and d.name.startswith("trellis"):
                dst = grok / "skills" / d.name
                copy_dir(d, dst, args.apply)
                skill_md = dst / "SKILL.md"
                if args.apply and skill_md.is_file():
                    skill_md.write_text(
                        normalize_markdown(rewrite(skill_md.read_text(encoding="utf-8"))),
                        encoding="utf-8",
                        newline="\n",
                    )
                n += 1
    print(f"  count: {n}\n")

    # 2) agents
    print("## agents")
    n = 0
    agents_src = claude / "agents"
    if agents_src.is_dir():
        for f in sorted(agents_src.glob("trellis*.md")):
            body = rewrite(f.read_text(encoding="utf-8"))
            body = ensure_pull_prelude(body, f.stem)
            write_text(grok / "agents" / f.name, body, args.apply)
            n += 1
    print(f"  count: {n}\n")

    # 3) commands (nested or flat)
    print("## commands")
    n = 0
    nested = claude / "commands" / "trellis"
    flat = claude / "commands"
    if nested.is_dir():
        for f in sorted(nested.glob("*.md")):
            name = f.stem
            if name.startswith("trellis-"):
                out = f"{name}.md"
            else:
                out = f"trellis-{name}.md"
            out = re.sub(r"trellis-trellis-", "trellis-", out)
            body = rewrite(f.read_text(encoding="utf-8"))
            write_text(grok / "commands" / out, body, args.apply)
            n += 1
    elif flat.is_dir():
        for f in sorted(flat.glob("trellis*.md")):
            body = rewrite(f.read_text(encoding="utf-8"))
            write_text(grok / "commands" / f.name, body, args.apply)
            n += 1
    print(f"  count: {n}\n")

    print("## hooks")
    print("  SKIP  (official Grok path is pull-based, no hooks)\n")

    if args.apply:
        (grok / "TRELLIS-GROK.md").write_text(
            """# Trellis on Grok (manual mirror of #433)

Generated by migrate-trellis-to-grok.py

## Layout
- .grok/skills/trellis-*
- .grok/agents/trellis-*.md
- .grok/commands/trellis-*.md
- hooks: not used

## Verify
```bash
grok inspect
```

## Note
When npm ships `trellis init --grok`, prefer the official command and
replace this mirror.
""",
            encoding="utf-8",
            newline="\n",
        )
        print(f"[done] wrote under {grok}")
    else:
        print("[dry-run] no files written. Re-run with --apply")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
