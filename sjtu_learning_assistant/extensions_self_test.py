"""隔离验证安装包内的 Skill 与 MCP 客户端；不读取用户配置或学习资料。"""
from __future__ import annotations
import json
import tempfile
import zipfile
from pathlib import Path
from sqlalchemy import create_engine
from .agent_runtime.presets import PresetLoader
from .agent_runtime.tools import AgentToolError, ReadOnlyToolRegistry
from .external_mcp import ExternalMCP
from .skill_packages import SkillPackageError, SkillPackages


def main():
    result = {"ok": False, "production_database_accesses": 0}
    try:
        with tempfile.TemporaryDirectory(prefix="sjtu-extensions-check-") as folder:
            root = Path(folder)
            loader = PresetLoader()
            store = SkillPackages(root / "agent_skills.json", loader.resource_root)
            archive = root / "reading.zip"
            with zipfile.ZipFile(archive, "w") as package:
                package.writestr("reader/skill/SKILL.md", "---\nname: package-test\ndescription: 验收\n---\n原始说明。")
                package.writestr("reader/README.md", "原结构说明")
                package.writestr("reader/scripts/run.py", "print('check')")
            skill = store.import_zip(archive)
            assert skill["entry"] == "skill/SKILL.md"
            assert store.read(skill["id"], "README.md")["text"] == "原结构说明"
            store.update(skill["id"], enabled=False)
            try:
                store.selected([skill["id"]])
            except SkillPackageError:
                pass
            else:
                raise AssertionError("关闭技能仍可使用")
            store.update(skill["id"], enabled=True)
            assert {p.id for p in loader.load()[1].values()} == {"general", "knowledge"}
            engine = create_engine("sqlite://")
            try:
                registry = ReadOnlyToolRegistry(engine, skill_packages=store, selected_skills=store.selected([skill["id"]]))
                registry.execute("read_skill_file", {"skill_id":skill["id"],"path":"README.md"}, loader.get("knowledge").allowed_tools)
                try:
                    registry.execute("run_skill_script", {"skill_id":skill["id"],"path":"scripts/run.py"}, loader.get("knowledge").allowed_tools)
                except AgentToolError:
                    pass
                else:
                    raise AssertionError("资料库助手允许执行脚本")
            finally:
                engine.dispose()
            mcp = ExternalMCP(root / "external-mcp.json")
            mcp.import_config({"mcpServers":{"DuckDuckGo":{"command":"uvx","args":["--from","duckduckgo-mcp-server[browser]==0.7.0","duckduckgo-mcp-server"],"env":{"DDG_REF_URL_THRESHOLD":"0"}}}})
            services = mcp.connect("DuckDuckGo")
            tools = services["items"][0]["tools"]
            assert {"search","fetch_content"}.issubset({t["name"] for t in tools})
            mcp.update("DuckDuckGo", permission="all", tools=["search","fetch_content"])
            definitions = mcp.definitions()
            assert len(definitions) == 2
            search = next(t for t in tools if t["name"] == "search")
            output, _ = mcp.call(search["model_name"], {"query":"Python asyncio documentation","max_results":3})
            result["duckduckgo_search_available"] = not output["isError"] and "No results" not in output["text"]
            result["duckduckgo_response"] = output["text"][:1800]
            fetch = next(t for t in tools if t["name"] == "fetch_content")
            output, _ = mcp.call(fetch["model_name"], {"url":"https://docs.python.org/3/library/asyncio.html","max_length":1200})
            result["duckduckgo_fetch_available"] = not output["isError"] and "asyncio" in output["text"]
            result["duckduckgo_fetch_response"] = output["text"][:1600]
            mcp.update("DuckDuckGo", enabled=False)
            assert mcp.definitions() == []
            result.update(ok=True, agents=2, zip_structure=True, skill_revocation=True, library_isolation=True, mcp_tools=len(tools), mcp_client_version=__import__("importlib.metadata",fromlist=["version"]).version("mcp"))
    except Exception as error:
        result["error"] = str(error)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["ok"] else 1
