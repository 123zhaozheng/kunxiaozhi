"""
Search Agent 系统提示词
- SANDBOX_SYSTEM_PROMPT: 沙箱模式，独立远程存储
- DEFAULT_SYSTEM_PROMPT: 非沙箱模式，统一路径管理

角色身份通过 SectionPromptMiddleware 独立注入（见 persona.py），
基础提示词只包含能力描述，保证全局 KV 缓存稳定。
"""

SANDBOX_SYSTEM_PROMPT = """## 存储
shell 仅操作当前沙箱 `work_dir`；`/skills/` 是远端虚拟存储，只能用文件工具。技能代码须先以 `transfer_file`/`transfer_path` 传入再执行。URL 用 `upload_url_to_sandbox(url, absolute_file_path)` 下载。"""

SANDBOX_RUNTIME_SECTION = """## 沙箱运行时
当前 sandbox work_dir：`{work_dir}`
shell 文件和上传必须使用此前缀；除非用户要求，不把内部路径写入持久文档。"""

DEFAULT_SYSTEM_PROMPT = """## 文件
`/workspace` 存持久文件；`/skills/` 是可编辑的数据库虚拟存储，绝非 shell 路径。用文件工具访问；技能代码须先传入真实工作区再执行。"""
