"""只读检查本机知识导航与正文容量，不调用模型，不打印课程正文或密钥。"""
import json
import sys
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy import create_engine
from sjtu_learning_assistant.database import APP_SUPPORT_DIR, DEFAULT_SQLITE_PATH
from sjtu_learning_assistant.local_settings import SettingsStore
from sjtu_learning_assistant.compiled_knowledge import CompiledKnowledge
from sjtu_learning_assistant.knowledge_wiki import CourseWiki


def main():
    engine = create_engine("sqlite+pysqlite:///file:" + quote(str(DEFAULT_SQLITE_PATH)) + "?mode=ro&uri=true")
    try:
        knowledge = CompiledKnowledge(settings=SettingsStore().load, projects_path=APP_SUPPORT_DIR / "local-projects.json", engine=engine)
        sources, documents, issues, truncated = knowledge._catalog()
        wiki = CourseWiki(knowledge).browse(limit=8)
        status = knowledge.compilation_status()
        body = knowledge.read(documents[0]["id"], max_chars=16000) if documents else None
        print(json.dumps({
            "source_count": len(sources), "document_count": len(documents),
            "issues": issues, "truncated": truncated,
            "wiki_navigable": True, "wiki_courses": wiki["count"], "catalog_complete": wiki["catalog_complete"],
            "progress": status,
            "body_read_status": body["read_status"] if body else None,
            "body_returned_chars": len(body["text"]) if body else None,
        }, ensure_ascii=False))
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
