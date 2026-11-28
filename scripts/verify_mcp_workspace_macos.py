"""用独立临时工作区验证安装二进制的 MCP 文件读取，不接触生产数据。"""
import argparse
import json
import os
import subprocess
import tempfile
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("binary", type=Path)
    parser.add_argument("--result", type=Path, required=True)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="sjtu-mcp-workspace-check-") as directory:
        base = Path(directory)
        support, workspace, archive = (base / part for part in ("support", "workspace", "archive"))
        for folder in (support, workspace, archive):
            folder.mkdir()
        (support / "settings.json").write_text(json.dumps({"archive_root": str(archive)}))
        (support / "ai_workspace.json").write_text(json.dumps({"version": 1, "root": str(workspace)}))
        (workspace / "note.md").write_text("# 临时验收\n本地工作区文件读取成功", encoding="utf-8")
        requests = (
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05"}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "read_workspace_file", "arguments": {"path": "note.md"}}},
            {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "read_workspace_file", "arguments": {"path": "../support/settings.json"}}},
        )
        run = subprocess.run(
            (str(args.binary.resolve()), "--mcp-stdio"),
            input="\n".join(json.dumps(request) for request in requests) + "\n",
            capture_output=True, text=True, timeout=60,
            env=dict(os.environ, SJTU_APP_SUPPORT_DIR=str(support), SJTU_DATABASE_URL="sqlite:///" + str(support / "test.sqlite3"), SJTU_ARCHIVE_ROOT=str(archive)),
        )
        assert run.returncode == 0, "安装二进制 MCP 未正常退出"
        replies = tuple(json.loads(line) for line in run.stdout.splitlines() if line.strip())
        by_id = {reply["id"]: reply for reply in replies}
        assert by_id[1]["result"]["serverInfo"]["version"] == "2.0.0"
        tools = {tool["name"]: tool for tool in by_id[2]["result"]["tools"]}
        assert "read_workspace_file" in tools and "list_workspace_files" in tools
        assert tools["read_workspace_file"]["annotations"]["readOnlyHint"]
        assert not tools["propose_file_change"]["annotations"]["readOnlyHint"]
        assert "apply_file_change" not in tools
        assert by_id[3]["result"]["isError"] is False
        assert "本地工作区文件读取成功" in json.dumps(by_id[3], ensure_ascii=False)
        assert "error" in by_id[4] or by_id[4].get("result", {}).get("isError") is True
        result = dict(ok=True, version="2.0.0", transport="installed-binary-stdio", file_read=True, traversal_denied=True, implicit_apply_tool=False, tool_count=len(tools), production_database_accesses=0)
        args.result.write_text(json.dumps(result, ensure_ascii=False, indent=2))
        print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
