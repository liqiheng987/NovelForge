from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import database


@dataclass
class MemoryConflict:
    category: str
    key: str
    existing: str
    incoming: str


class MemoryEngine:
    """Project-scoped structured memory facade used by chat and API routes."""

    def snapshot(self, project_id: str) -> dict[str, Any]:
        return {
            "facts": database.list_facts(project_id),
            "grouped": database.project_memory(project_id),
            "rules": database.list_universe_rules(project_id),
            "pinned": database.list_pinned_materials(project_id),
        }

    def facts(self, project_id: str) -> list[dict[str, Any]]:
        return database.list_facts(project_id)

    def upsert_fact(self, project_id: str, category: str, key: str, value: str, source: str = "ai") -> dict[str, Any]:
        conflicts = self.check_conflicts(project_id, category, key, value)
        if conflicts:
            raise ValueError(f"事实冲突：{category}/{key} 已存在不同值")
        return database.upsert_fact(project_id, category, key, value, source)

    def delete_fact(self, fact_id: str) -> None:
        database.delete_fact(fact_id)

    def check_conflicts(self, project_id: str, category: str, key: str, value: str) -> list[MemoryConflict]:
        normalized_key = key.strip()
        normalized_value = value.strip()
        return [
            MemoryConflict(category, normalized_key, str(item["value"]), normalized_value)
            for item in database.list_facts(project_id)
            if str(item["category"]) == category
            and str(item["key"]).casefold() == normalized_key.casefold()
            and str(item["value"]).strip() != normalized_value
        ]

    def prompt_context(self, project_id: str) -> str:
        snapshot = self.snapshot(project_id)
        lines: list[str] = []
        for rule in snapshot["rules"]:
            lines.append(f"- [{rule['category']}] {rule['key']}: {rule['value']}")
        if lines:
            lines.insert(0, "[宇宙铁律]")
        facts = snapshot["facts"]
        if facts:
            lines.append("[结构化事实]")
            lines.extend(f"- [{fact['category']}] {fact['key']}: {fact['value']}" for fact in facts)
        pinned = snapshot["pinned"]
        if pinned:
            lines.append("[常驻素材]")
            lines.extend(f"- {item['display_name']}: {item['summary']}" for item in pinned)
        return "\n".join(lines)


memory_engine = MemoryEngine()
