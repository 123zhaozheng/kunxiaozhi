"""Pure helpers for knowledge-graph extraction: prompt, tolerant JSON parsing, normalisation."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any

ENTITY_NAME_MAX = 512
ENTITY_LABEL_MAX = 128
RELATION_LABEL_MAX = 256
ATTRIBUTE_VALUE_MAX = 500
ATTRIBUTES_MAX = 20
DEFAULT_ENTITY_LABEL = "Entity"
DEFAULT_RELATION_LABEL = "RELATED_TO"
SCHEMA_MAX = 1000
CHUNK_TEXT_MAX = 6000

EXTRACTION_PROMPT = """你是知识图谱构建助手。请从给定文本中抽取实体与实体间的关系。

要求：
1. 实体名称使用文本中的原始表述（保持原语言），类型用简短名词（如 Person、Organization、Concept）。
2. 只抽取文本明确表达的关系，不要臆测；关系类型用简短动词短语或大写蛇形命名。
3. 只输出一个严格的 JSON 对象，不要任何解释或代码围栏，格式：
{{"entities": [{{"name": "实体名", "type": "类型", "attributes": {{"属性名": "属性值"}}}}],
  "relations": [{{"source": "源实体名", "target": "目标实体名", "type": "关系类型"}}]}}
4. 没有可抽取内容时输出 {{"entities": [], "relations": []}}。
{schema_block}
文本：
{text}
"""

_FENCE = re.compile(r"^```[a-zA-Z0-9_-]*\s*|\s*```$")


class ExtractionParseError(ValueError):
    """The model reply did not contain a usable JSON object."""


def normalize_entity_name(text: str) -> str:
    return " ".join((text or "").strip().lower().split())


def hash32(value: str) -> str:
    return hashlib.md5(value.encode("utf-8"), usedforsecurity=False).hexdigest()


def entity_id(kb_id: str, normalized_name: str, label: str) -> str:
    return hash32(f"{kb_id}:{normalized_name}:{label}")


def triple_id(kb_id: str, src: tuple[str, str], label: str, tgt: tuple[str, str]) -> str:
    return hash32(f"{kb_id}:{src[0]}:{src[1]}:{label}:{tgt[0]}:{tgt[1]}")


def build_prompt(text: str, schema: str | None) -> str:
    schema = (schema or "").strip()[:SCHEMA_MAX]
    block = f"\n抽取约束（请遵循）：\n{schema}\n" if schema else ""
    return EXTRACTION_PROMPT.format(schema_block=block, text=text[:CHUNK_TEXT_MAX])


def message_text(content: Any) -> str:
    """Flatten a chat-model reply (string or content-block list) to text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and item.get("type", "text") == "text":
                parts.append(str(item.get("text", "")))
        return "".join(parts)
    return str(content or "")


def parse_extraction_json(raw: str) -> dict[str, Any]:
    """Strip code fences, ``json.loads``; on failure retry on the first ``{`` .. last ``}``."""
    text = _FENCE.sub("", (raw or "").strip()).strip()
    candidates = [text]
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        candidates.append(text[start : end + 1])
    for candidate in candidates:
        try:
            data = json.loads(candidate)
        except (ValueError, TypeError):
            continue
        if isinstance(data, dict):
            return data
    raise ExtractionParseError("模型返回的内容不是有效的 JSON")


@dataclass
class EntityRec:
    id: str
    name: str
    normalized_name: str
    label: str
    attributes: dict[str, str] = field(default_factory=dict)


@dataclass
class TripleRec:
    id: str
    source_id: str
    target_id: str
    label: str
    text: str


@dataclass
class Extraction:
    entities: list[EntityRec] = field(default_factory=list)
    triples: list[TripleRec] = field(default_factory=list)


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _attributes(raw: Any) -> dict[str, str]:
    if not isinstance(raw, dict):
        return {}
    cleaned: dict[str, str] = {}
    for key, value in raw.items():
        name = str(key).strip().replace(".", "_").lstrip("$")
        if isinstance(value, dict | list):
            value = json.dumps(value, ensure_ascii=False)
        text = str(value).strip() if value is not None else ""
        if name and text and len(cleaned) < ATTRIBUTES_MAX:
            cleaned[name[:64]] = text[:ATTRIBUTE_VALUE_MAX]
    return cleaned


def normalize_extraction(kb_id: str, data: dict[str, Any]) -> Extraction:
    """Apply Yuxi's drop/default rules and derive deterministic ids.

    Entities with a name > 512 or type > 128 chars are dropped along with their relations;
    relation types > 256 chars are dropped; defaults are ``Entity`` / ``RELATED_TO``.
    """
    result = Extraction()
    by_key: dict[tuple[str, str], EntityRec] = {}
    first_label: dict[str, str] = {}
    dropped: set[str] = set()

    def register(name: str, label: str, attributes: dict[str, str]) -> EntityRec | None:
        normalized = normalize_entity_name(name)
        if not normalized:
            return None
        if len(name) > ENTITY_NAME_MAX or len(label) > ENTITY_LABEL_MAX:
            dropped.add(normalized)
            return None
        key = (normalized, label)
        rec = by_key.get(key)
        if rec is None:
            rec = EntityRec(entity_id(kb_id, normalized, label), name, normalized, label, {})
            by_key[key] = rec
            first_label.setdefault(normalized, label)
            result.entities.append(rec)
        rec.attributes.update(attributes)
        return rec

    raw_entities = data.get("entities")
    for item in raw_entities if isinstance(raw_entities, list) else []:
        if isinstance(item, str):
            item = {"name": item}
        if not isinstance(item, dict):
            continue
        name = _text(item.get("name"))
        label = _text(item.get("type") or item.get("label")) or DEFAULT_ENTITY_LABEL
        register(name, label, _attributes(item.get("attributes")))

    seen: set[str] = set()
    raw_relations = data.get("relations")
    for item in raw_relations if isinstance(raw_relations, list) else []:
        if not isinstance(item, dict):
            continue
        ends: list[EntityRec] = []
        for key in (("source", "head", "from"), ("target", "tail", "to")):
            name = next((_text(item.get(k)) for k in key if _text(item.get(k))), "")
            normalized = normalize_entity_name(name)
            if not normalized or normalized in dropped:
                break
            label = first_label.get(normalized, DEFAULT_ENTITY_LABEL)
            rec = by_key.get((normalized, label)) or register(name, label, {})
            if rec is None:
                break
            ends.append(rec)
        if len(ends) != 2 or ends[0].id == ends[1].id:
            continue
        label = _text(item.get("type") or item.get("label") or item.get("relation"))
        label = label or DEFAULT_RELATION_LABEL
        if len(label) > RELATION_LABEL_MAX:
            continue
        src, tgt = ends
        tid = triple_id(
            kb_id, (src.normalized_name, src.label), label, (tgt.normalized_name, tgt.label)
        )
        if tid in seen:
            continue
        seen.add(tid)
        result.triples.append(
            TripleRec(tid, src.id, tgt.id, label, f"{src.name} → {label} → {tgt.name}")
        )
    return result
