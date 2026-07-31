# Technical Design

The cleanup follows the latest architecture already present in the worktree.

- Persona persistence contains exact Marketplace Skill names only.
- Persona activation reads current Marketplace metadata and emits transient
  `skill_hints`; it never writes user `skill_files` and never mounts an overlay.
- Builtin Skills remain in their own collections and are merged at read time
  after role filtering. User Skill names, including disabled names, shadow a
  same-name Builtin. Builtin preferences use separate metadata keys.
- Ordinary Marketplace publication/install remains independent of Persona
  creation.

Only code proven to belong to the superseded flow is removed. Compatibility
behavior for persisted legacy documents is retained through schema defaults and
ignored extra fields.
