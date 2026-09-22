#!/usr/bin/env python3
"""Fail CI when Python dependency groups violate the repository license policy."""

from __future__ import annotations

import importlib.metadata
import re
import sys
from pathlib import Path

from packaging.requirements import Requirement

ROOT = Path(__file__).resolve().parents[1]
RUNTIME_FILE = ROOT / "requirements.txt"
OPTIONAL_FILE = ROOT / "requirements-postgres.txt"
DEV_FILE = ROOT / "requirements-dev.txt"
STRONG_COPYLEFT = re.compile(r"(?:^|[^l])(?:a?gpl)(?:[- v]|$)", re.IGNORECASE)
PERMISSIVE_ALTERNATIVES = ("apache", "bsd", "isc", "mit")


def canonical(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def requirement_names(path: Path) -> set[str]:
    names: set[str] = set()
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line or line.startswith("-r "):
            continue
        names.add(canonical(Requirement(line).name))
    return names


def license_for(distribution: importlib.metadata.Distribution) -> str:
    metadata = distribution.metadata
    expression = metadata.get("License-Expression")
    if expression:
        return expression.strip()
    value = metadata.get("License")
    if value and "\n" not in value and len(value) < 300:
        return value.strip()
    classifiers = metadata.get_all("Classifier") or []
    licenses = [
        item.removeprefix("License :: OSI Approved :: ")
        for item in classifiers
        if item.startswith("License :: OSI Approved :: ")
    ]
    return " OR ".join(licenses)


def dependency_closure(roots: set[str]) -> dict[str, importlib.metadata.Distribution]:
    installed = {
        canonical(dist.metadata["Name"]): dist
        for dist in importlib.metadata.distributions()
        if dist.metadata.get("Name")
    }
    pending = list(roots)
    closure: dict[str, importlib.metadata.Distribution] = {}
    while pending:
        name = pending.pop()
        if name in closure:
            continue
        distribution = installed.get(name)
        if distribution is None:
            raise RuntimeError(f"未安装 Python 依赖：{name}")
        closure[name] = distribution
        for raw_requirement in distribution.requires or []:
            requirement = Requirement(raw_requirement)
            if requirement.marker and not requirement.marker.evaluate({"extra": ""}):
                continue
            pending.append(canonical(requirement.name))
    return closure


def has_pyinstaller_exception(distribution: importlib.metadata.Distribution, license_name: str) -> bool:
    copying = distribution.read_text("licenses/COPYING.txt") or ""
    text = f"{license_name}\n{copying}".casefold()
    return "gpl" in text and "bootloader exception" in text and "special exception" in text


def has_permissive_or_branch(license_name: str) -> bool:
    normalized = license_name.casefold()
    return " or " in normalized and any(item in normalized for item in PERMISSIVE_ALTERNATIVES)


def check_group(label: str, roots: set[str], *, allow_pyinstaller: bool) -> list[str]:
    errors: list[str] = []
    try:
        closure = dependency_closure(roots)
    except RuntimeError as exc:
        return [str(exc)]
    for name, distribution in sorted(closure.items()):
        license_name = license_for(distribution)
        if not license_name:
            errors.append(f"{label}: {name} 缺少可验证许可证元数据")
            continue
        if name == "pyinstaller":
            if not allow_pyinstaller:
                errors.append("runtime: PyInstaller 只能位于 requirements-dev.txt")
            elif not has_pyinstaller_exception(distribution, license_name):
                errors.append("dev: 无法在已安装 PyInstaller 中验证 bootloader exception")
            continue
        if STRONG_COPYLEFT.search(license_name) and not has_permissive_or_branch(license_name):
            errors.append(f"{label}: 禁止的强 copyleft 许可证：{name} ({license_name})")
    return errors


def main() -> int:
    runtime = requirement_names(RUNTIME_FILE)
    optional = requirement_names(OPTIONAL_FILE)
    dev = requirement_names(DEV_FILE) - runtime
    if "psycopg" in runtime or "psycopg-binary" in runtime:
        print("错误：psycopg 只能位于 requirements-postgres.txt", file=sys.stderr)
        return 1
    if optional != {"psycopg"}:
        print("错误：requirements-postgres.txt 只能声明 psycopg 可选迁移依赖", file=sys.stderr)
        return 1
    errors = check_group("runtime", runtime, allow_pyinstaller=False)
    errors.extend(check_group("dev", dev, allow_pyinstaller=True))
    for error in errors:
        print(f"错误：{error}", file=sys.stderr)
    if errors:
        return 1
    print(
        "Python 许可证检查通过：运行时无 GPL/AGPL 强 copyleft；"
        "PyInstaller bootloader exception 已验证；psycopg 仅为可选迁移依赖。"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
