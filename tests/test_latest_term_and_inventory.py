from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock
import json

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sjtu_learning_assistant.course_scope import latest_term_courses
from sjtu_learning_assistant.desktop_learning_service import DesktopLearningService
from sjtu_learning_assistant.models import Base, Course, Assignment, CourseFile
from sjtu_learning_assistant.material_tree import build_material_tree
from sjtu_learning_assistant.markdown_progress import course_progress
from sjtu_learning_assistant.compiled_knowledge import CompiledKnowledge


class LatestTermTests(TestCase):
    def test_dates_take_priority_over_id_and_name_and_keep_term_peers(self):
        rows = [dict(id=1, term=dict(id=900, name='2025-2026 Fall', start_at='2025-08-13T16:00:00Z')),
                dict(id=2, term=dict(id=30, name='2026-2027 Fall', start_at='2026-08-13T16:00:00Z')),
                dict(id=3, term=dict(id=30, name='2026-2027 Fall')),
                dict(id=4)]
        self.assertEqual([2, 3], [row['id'] for row in latest_term_courses(rows)])
        self.assertEqual([2], [row['id'] for row in latest_term_courses([
            dict(id=1, term=dict(name='2025-2026 Fall')), dict(id=2, term=dict(name='2025-2026 Spring'))])])
        unknown = [dict(id=1)]
        self.assertIs(unknown, latest_term_courses(unknown))

    def test_database_assignment_filters_latest_term_and_unknown_materials_hidden_without_deletion(self):
        engine = create_engine('sqlite://')
        self.addCleanup(engine.dispose)
        Base.metadata.create_all(engine)
        now = datetime.now(timezone.utc)
        with Session(engine) as session, session.begin():
            for i, label in enumerate(['2025-2026 Spring', '2026-2027 Fall', None], 1):
                course = Course(source_id=str(i), name=str(i), term_name=label, raw_data={})
                session.add(course); session.flush()
                session.add(Assignment(source_id=str(i), name='作业'+str(i), course_id=course.id, is_active=True, last_seen_at=now, raw_data={}))
                session.add(CourseFile(source_id=str(i), course_id=course.id, display_name='lecture.pdf', is_active=True, last_seen_at=now, raw_data={}))
        service = DesktopLearningService(engine=engine)
        self.assertEqual(['作业2'], [item['name'] for item in service.assignments_list('all')['items']])
        with Session(engine) as session:
            tree = build_material_tree(session)
            self.assertNotIn('未识别学期', str(tree))
            self.assertEqual(3, len(list(session.scalars(select(CourseFile)))))

    def test_online_assignments_only_request_latest_courses(self):
        canvas = Mock()
        canvas.courses.return_value = [dict(id=1, term=dict(name='2025-2026 Fall')), dict(id=2, term=dict(name='2026-2027 Fall'))]
        canvas.assignments.return_value = []
        service = DesktopLearningService(assignment_service=SimpleNamespace(canvas=canvas))
        service.assignments_list('all')
        canvas.assignments.assert_called_once_with(2)


class InventoryTests(TestCase):
    def test_all_compiler_formats_and_unregistered_sources_count_without_writes(self):
        with TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            paths = []
            for suffix in ('.pdf', '.pptx', '.ppt', '.pptm', '.docm', '.xlsm', '.ppsm', '.potm', '.docx'):
                path = root / ('lecture'+suffix); path.write_bytes(b'original'); paths.append(path)
            output = root / 'Knowledge Markdown'
            output.mkdir(); (output / 'internal.pdf').write_bytes(b'not original')
            original_hashes = {p: p.read_bytes() for p in paths}
            result = course_progress(root, paths[:3])
            self.assertEqual(8, result['total'])
            self.assertEqual(8, result['pending'])
            self.assertEqual(8, result['inventory']['unregistered'])
            self.assertEqual(5, result['inventory']['untracked_supported'])
            self.assertEqual(1, result['inventory']['unsupported_local'])
            self.assertFalse((output / '99_System').exists())
            self.assertEqual(original_hashes, {p: p.read_bytes() for p in paths})

    def test_project_intermediate_page_files_do_not_consume_document_budget(self):
        from unittest.mock import patch
        with TemporaryDirectory() as temp:
            root = Path(temp).resolve(); vault = root / 'vault'; vault.mkdir()
            (vault / 'pages').mkdir()
            for i in range(20): (vault / 'pages' / (str(i)+'.md')).write_text('page')
            (vault / 'final.md').write_text('final')
            registry = root / 'projects.json'
            registry.write_text(json.dumps(dict(version=1,projects=[dict(id='a'*24,name='课程',target_root=str(vault))])))
            service = CompiledKnowledge(lambda: SimpleNamespace(archive_root=str(root/'archive')),registry)
            with patch('sjtu_learning_assistant.compiled_knowledge.MAX_ENTRIES', 5):
                result = service.sources()
            self.assertEqual(1,result['count'])
            self.assertFalse(result['truncated'])
