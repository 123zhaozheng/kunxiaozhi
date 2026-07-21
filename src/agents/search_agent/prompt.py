"""
Search Agent 系统提示词
- SANDBOX_SYSTEM_PROMPT: 沙箱模式，独立远程存储
- DEFAULT_SYSTEM_PROMPT: 非沙箱模式，统一路径管理

角色身份通过 SectionPromptMiddleware 独立注入（见 persona.py），
基础提示词只包含能力描述，保证全局 KV 缓存稳定。
"""

from src.agents.core.harness_prompt_overrides import select_harness_text

_LEGACY_SANDBOX_SYSTEM_PROMPT = """## Storage Architecture (CRITICAL)

| System | Paths | Access |
|--------|-------|--------|
| Sandbox Local | active sandbox `work_dir` | shell commands |
| Remote Storage | `/skills/` | read/write/edit_file tools |

`/skills/` is virtual remote storage, not a sandbox filesystem path. Use file tools for `/skills/`; never shell-access it (`python /skills/x.py`, `cat /skills/x.md`, `cp /skills/* .`). To run skill code, transfer it into the current sandbox work_dir with `transfer_file`/`transfer_path`, then execute the copied file.

## URL File Upload
Use `upload_url_to_sandbox(url, file_path)` to download URLs to sandbox. `file_path` must be absolute inside the current sandbox work_dir.
"""

_LEGACY_SANDBOX_RUNTIME_SECTION = """## Sandbox Runtime

Current sandbox work_dir: `{work_dir}`

Use this absolute directory for shell-created files and absolute `upload_url_to_sandbox` paths. Keep this runtime value out of durable docs unless the user specifically asks for internal paths.
"""

_LEGACY_DEFAULT_SYSTEM_PROMPT = """## File System
| Path | Purpose |
|------|---------|
| `/workspace` | Persistent files |
| `/skills/` | Skill library (editable, virtual — DB-backed) |

`/skills/` is virtual storage, not a real filesystem directory. Use `ls`, `read_file`, `write_file`, and `edit_file` for skills; never shell-access `/skills/` (`ls -la /skills/`, `cat /skills/x.md`, `python /skills/x.py`). To execute a skill script, first copy it into `/workspace` or the sandbox work directory via `transfer_file`/`transfer_path`.
"""

SANDBOX_SYSTEM_PROMPT = select_harness_text(
    legacy=_LEGACY_SANDBOX_SYSTEM_PROMPT,
    compact_en="""## Storage
Shell uses the active sandbox `work_dir`; `/skills/` is remote virtual storage accessed only by file tools. Transfer skill code with `transfer_file`/`transfer_path` before execution. Download URLs with `upload_url_to_sandbox(url, absolute_file_path)`.""",
    compact_zh="""## 存储
shell 仅操作当前沙箱 `work_dir`；`/skills/` 是远端虚拟存储，只能用文件工具。技能代码须先以 `transfer_file`/`transfer_path` 传入再执行。URL 用 `upload_url_to_sandbox(url, absolute_file_path)` 下载。""",
)

SANDBOX_RUNTIME_SECTION = select_harness_text(
    legacy=_LEGACY_SANDBOX_RUNTIME_SECTION,
    compact_en="""## Sandbox Runtime
Current sandbox work_dir: `{work_dir}`
Use this absolute path for shell files and uploads; do not persist it in user documents unless requested.""",
    compact_zh="""## 沙箱运行时
当前 sandbox work_dir：`{work_dir}`
shell 文件和上传必须使用此前缀；除非用户要求，不把内部路径写入持久文档。""",
)

DEFAULT_SYSTEM_PROMPT = select_harness_text(
    legacy=_LEGACY_DEFAULT_SYSTEM_PROMPT,
    compact_en="""## Files
`/workspace` stores persistent files. `/skills/` is editable virtual DB storage, never a shell path. Use file tools; transfer skill code to a real workspace before execution.""",
    compact_zh="""## 文件
`/workspace` 存持久文件；`/skills/` 是可编辑的数据库虚拟存储，绝非 shell 路径。用文件工具访问；技能代码须先传入真实工作区再执行。""",
)

DEFERRED_TOOL_GUIDE = ""
