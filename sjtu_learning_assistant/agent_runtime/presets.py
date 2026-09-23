"""安全加载本地 agent preset 清单与技能提示词。"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

PRESET_ID = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
KNOWN_TOOLS = frozenset(
    {
        "list_courses",
        "search_course_files",
        "list_course_files",
        "get_deadlines",
        "search_messages",
        "get_message_detail",
        "get_material_tree",
    }
)


class PresetError(RuntimeError):
    """Preset 配置无效或路径不安全。"""


@dataclass(frozen=True)
class AgentPreset:
    id: str
    name: str
    description: str
    allowed_tools: tuple[str, ...]
    skill_prompt: str

    def dto(self) -> dict[str, object]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "allowed_tools": list(self.allowed_tools),
        }


def default_resource_root() -> Path:
    frozen_root = getattr(sys, "_MEIPASS", None)
    if frozen_root:
        return Path(frozen_root)
    return Path(__file__).resolve().parents[2]


class PresetLoader:
    def __init__(self, resource_root: Path | None = None) -> None:
        self.resource_root = (resource_root or default_resource_root()).resolve()
        self.preset_root = (self.resource_root / "agent_presets").resolve()

    @staticmethod
    def _text(value: object, *, field: str, limit: int) -> str:
        if type(value) is not str or not value.strip() or len(value) > limit or "\x00" in value:
            raise PresetError(f"Preset {field} 无效。")
        return value.strip()

    def _skill_path(self, value: object) -> Path:
        relative = self._text(value, field="skill", limit=160)
        candidate = Path(relative)
        if (
            candidate.is_absolute()
            or candidate.suffix.casefold() != ".md"
            or any(part in {".", ".."} for part in candidate.parts)
        ):
            raise PresetError("Preset skill 路径不安全。")
        current = self.preset_root
        try:
            for part in candidate.parts:
                current = current / part
                if current.is_symlink():
                    raise PresetError("Preset skill 路径不安全。")
            resolved = current.resolve(strict=True)
            skills_root = (self.preset_root / "skills").resolve(strict=True)
            resolved.relative_to(skills_root)
        except (FileNotFoundError, OSError, ValueError):
            raise PresetError("Preset skill 路径不安全。") from None
        if not resolved.is_file() or resolved.is_symlink():
            raise PresetError("Preset skill 路径不安全。")
        return resolved

    def load(self) -> tuple[str, dict[str, AgentPreset]]:
        manifest_path = self.preset_root / "manifest.json"
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            raise PresetError("无法加载 agent preset 清单。") from None
        if type(manifest) is not dict or set(manifest) != {"version", "default_preset", "presets"}:
            raise PresetError("Agent preset 清单格式无效。")
        if manifest["version"] != 1 or type(manifest["presets"]) is not list:
            raise PresetError("Agent preset 清单版本或内容无效。")
        raw_presets = manifest["presets"]
        if not 1 <= len(raw_presets) <= 32:
            raise PresetError("Agent preset 数量无效。")
        result: dict[str, AgentPreset] = {}
        for raw in raw_presets:
            if type(raw) is not dict or set(raw) != {
                "id", "name", "description", "skill", "allowed_tools"
            }:
                raise PresetError("Agent preset 字段无效。")
            preset_id = self._text(raw["id"], field="id", limit=32)
            if PRESET_ID.fullmatch(preset_id) is None or preset_id in result:
                raise PresetError("Agent preset id 无效或重复。")
            allowed = raw["allowed_tools"]
            if (
                type(allowed) is not list
                or len(allowed) > len(KNOWN_TOOLS)
                or any(type(item) is not str for item in allowed)
                or len(set(allowed)) != len(allowed)
                or not set(allowed).issubset(KNOWN_TOOLS)
            ):
                raise PresetError("Agent preset 包含非法工具。")
            skill_path = self._skill_path(raw["skill"])
            try:
                skill_prompt = skill_path.read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                raise PresetError("无法读取 agent skill。") from None
            if not skill_prompt.strip() or len(skill_prompt) > 20_000:
                raise PresetError("Agent skill 内容无效。")
            result[preset_id] = AgentPreset(
                id=preset_id,
                name=self._text(raw["name"], field="name", limit=80),
                description=self._text(raw["description"], field="description", limit=240),
                allowed_tools=tuple(allowed),
                skill_prompt=skill_prompt.strip(),
            )
        default_id = self._text(manifest["default_preset"], field="default_preset", limit=32)
        if default_id not in result:
            raise PresetError("默认 agent preset 不存在。")
        return default_id, result

    def get(self, preset_id: object | None = None) -> AgentPreset:
        default_id, presets = self.load()
        selected = default_id if preset_id is None else self._text(preset_id, field="id", limit=32)
        if selected not in presets:
            raise PresetError("Agent preset 不存在。")
        return presets[selected]

    def list_presets(self) -> list[dict[str, object]]:
        default_id, presets = self.load()
        return [
            {**preset.dto(), "is_default": preset.id == default_id}
            for preset in presets.values()
        ]

    def validate_allowed_tools(self, names: Iterable[str]) -> None:
        if not set(names).issubset(KNOWN_TOOLS):
            raise PresetError("包含未知 agent 工具。")
