"""Native smoke test of the frozen search MCP helper; no internet request."""
from __future__ import annotations
import argparse
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sjtu_learning_assistant.external_mcp import ExternalMCP


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle', type=Path, required=True)
    args = parser.parse_args()
    if sys.platform not in ('darwin', 'win32'):
        raise RuntimeError('Packaged MCP check requires native macOS or Windows')
    bundle = args.bundle.resolve(strict=True)
    exe = bundle / ('Contents/MacOS/SJTU Learning Assistant' if sys.platform == 'darwin' else 'SJTU Learning Assistant.exe')
    if not exe.is_file():
        raise RuntimeError('Missing actual packaged executable')
    with tempfile.TemporaryDirectory(prefix='sjtu-packaged-mcp-') as directory:
        mcp = ExternalMCP(Path(directory) / 'mcp.json')
        mcp.import_config({'mcpServers': {'packaged': {'command': str(exe), 'args': ['--web-search-mcp']}}})
        service = mcp.connect('packaged')['items'][0]
        tools = service['tools']
        if len(tools) != 1 or tools[0]['name'] != 'search' or not tools[0]['read_only']:
            raise RuntimeError('Invalid actual packaged MCP tools')
        # Invalid query is rejected before HTTP. This tests UTF-8 JSON-RPC calls
        # through the real frozen process without touching any user data.
        result, _ = mcp.call(tools[0]['model_name'], {'query': ''})
        if not result['isError'] or '关键词需要' not in result['text']:
            raise RuntimeError('Actual packaged MCP validation/UTF-8 call failed')
    print('Packaged MCP: discovery, call, UTF-8 and read-only annotation passed; no web request')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
