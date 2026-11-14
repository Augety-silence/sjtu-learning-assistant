"""安全加载本地 agent preset 清单与技能提示词。"""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import uuid
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
        "search_ai_attachments",
        "read_ai_attachment_text",
        "read_course_file_text",
        "list_compiled_knowledge",
        "search_compiled_knowledge",
        "read_compiled_knowledge",
        "list_workspace_files",
        "read_workspace_file",
        "propose_file_change",
    }
)
USER_SKILL_ID = re.compile(r"^u-[a-z0-9]{4,24}$")
MAX_USER_SKILLS = 32
MAX_SKILL_REFERENCES = 10
MAX_SKILL_INSTRUCTIONS = 20_000


class PresetError(RuntimeError):
    """Preset 配置无效或路径不安全。"""


@dataclass(frozen=True)
class AgentPreset:
    id: str
    name: str
    description: str
    allowed_tools: tuple[str, ...]
    skill_prompt: str
    source: str = "builtin"
    reference_attachment_ids: tuple[int, ...] = ()

    def dto(self) -> dict[str, object]:
        result: dict[str, object] = {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "allowed_tools": list(self.allowed_tools),
            "source": self.source,
        }
        if self.source == "user":
            # 只有用户自建技能会把指令回传给界面编辑；内置技能提示词不外露。
            result["instructions"] = self.skill_prompt
            result["reference_attachment_ids"] = list(self.reference_attachment_ids)
        return result


def _clean_skill_text(value: object, field: str, limit: int, *, required: bool) -> str:
    if value is None and not required:
        return ""
    if type(value) is not str or len(value) > limit or "\x00" in value:
        raise PresetError(f"技能{field}无效。")
    clean = value.strip()
    if required and not clean:
        raise PresetError(f"技能{field}不能为空。")
    return clean


def validate_user_skill(raw: object, *, existing_ids: Iterable[str] = ()) -> dict[str, object]:
    """校验一条用户技能；返回可直接写入 JSON 的规范化字典。"""
    if type(raw) is not dict or set(raw) - {
        "id", "name", "description", "instructions", "allowed_tools",
        "reference_attachment_ids", "updated_at",
    }:
        raise PresetError("技能字段无效。")
    skill_id = raw.get("id")
    if skill_id is None:
        skill_id = "u-" + uuid.uuid4().hex[:10]
    if type(skill_id) is not str or USER_SKILL_ID.fullmatch(skill_id) is None:
        raise PresetError("技能标识无效。")
    allowed = raw.get("allowed_tools", [])
    if (
        type(allowed) is not list
        or any(type(item) is not str for item in allowed)
        or len(set(allowed)) != len(allowed)
        or not set(allowed).issubset(KNOWN_TOOLS)
    ):
        raise PresetError("技能包含未知工具。")
    references = raw.get("reference_attachment_ids", [])
    if (
        type(references) is not list
        or len(references) > MAX_SKILL_REFERENCES
        or any(type(item) is not int or not 1 <= item <= 2**63 - 1 for item in references)
    ):
        raise PresetError(f"技能参考资料最多 {MAX_SKILL_REFERENCES} 个附件。")
    del existing_ids
    return {
        "id": skill_id,
        "name": _clean_skill_text(raw.get("name"), "名称", 40, required=True),
        "description": _clean_skill_text(raw.get("description"), "说明", 240, required=False),
        "instructions": _clean_skill_text(
            raw.get("instructions"), "指令", MAX_SKILL_INSTRUCTIONS, required=True
        ),
        "allowed_tools": [tool for tool in sorted(KNOWN_TOOLS) if tool in set(allowed)],
        "reference_attachment_ids": list(dict.fromkeys(references)),
        "updated_at": raw.get("updated_at") if type(raw.get("updated_at")) is str else None,
    }


class UserSkillStore:
    """用户自建技能：保存在应用数据目录的单个 JSON 文件中，原子写入。"""

    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> list[dict[str, object]]:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return []
        except (OSError, UnicodeError, json.JSONDecodeError):
            # 文件损坏不应让内置助手不可用；跳过全部用户技能。
            return []
        raw_skills = payload.get("skills") if type(payload) is dict else None
        if type(raw_skills) is not list:
            return []
        result: list[dict[str, object]] = []
        seen: set[str] = set()
        for raw in raw_skills[:MAX_USER_SKILLS]:
            try:
                skill = validate_user_skill(raw)
            except PresetError:
                continue
            if skill["id"] in seen:
                continue
            seen.add(str(skill["id"]))
            result.append(skill)
        return result

    def _write(self, skills: list[dict[str, object]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(
            prefix=".agent-skills-", suffix=".json", dir=self.path.parent
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump({"version": 1, "skills": skills}, stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        except OSError:
            Path(temporary).unlink(missing_ok=True)
            raise PresetError("无法保存技能，请检查应用数据目录权限。") from None

    def save(self, raw: object, *, updated_at: str) -> dict[str, object]:
        skill = validate_user_skill(raw)
        skill["updated_at"] = updated_at
        skills = self.load()
        index = next((i for i, item in enumerate(skills) if item["id"] == skill["id"]), None)
        if index is None:
            if type(raw) is dict and raw.get("id") is not None:
                raise PresetError("要编辑的技能不存在。")
            if len(skills) >= MAX_USER_SKILLS:
                raise PresetError(f"最多保存 {MAX_USER_SKILLS} 个自定义技能。")
            skills.append(skill)
        else:
            skills[index] = skill
        self._write(skills)
        return skill

    def delete(self, skill_id: object) -> bool:
        if type(skill_id) is not str or USER_SKILL_ID.fullmatch(skill_id) is None:
            raise PresetError("技能标识无效。")
        skills = self.load()
        remaining = [item for item in skills if item["id"] != skill_id]
        if len(remaining) == len(skills):
            return False
        self._write(remaining)
        return True


def default_resource_root() -> Path:
    frozen_root = getattr(sys, "_MEIPASS", None)
    if frozen_root:
        return Path(frozen_root)
    return Path(__file__).resolve().parents[2]


class PresetLoader:
    def __init__(
        self,
        resource_root: Path | None = None,
        *,
        user_skills_path: Path | None = None,
    ) -> None:
        self.resource_root = (resource_root or default_resource_root()).resolve()
        self.preset_root = (self.resource_root / "agent_presets").resolve()
        self.user_skills = UserSkillStore(user_skills_path) if user_skills_path else None

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
        if self.user_skills is not None:
            for skill in self.user_skills.load():
                if skill["id"] in result:
                    continue
                result[str(skill["id"])] = AgentPreset(
                    id=str(skill["id"]),
                    name=str(skill["name"]),
                    description=str(skill["description"]) or "自定义技能",
                    allowed_tools=tuple(skill["allowed_tools"]),  # type: ignore[arg-type]
                    skill_prompt=str(skill["instructions"]),
                    source="user",
                    reference_attachment_ids=tuple(skill["reference_attachment_ids"]),  # type: ignore[arg-type]
                )
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
        if isinstance(names, str) or not set(names).issubset(KNOWN_TOOLS):
            raise PresetError("包含未知 agent 工具。")
