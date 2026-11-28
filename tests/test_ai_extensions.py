import json
import stat
import sys
import tempfile
import unittest
import zipfile
from unittest.mock import patch
from pathlib import Path
from sjtu_learning_assistant.skill_packages import SkillPackages, SkillPackageError, script_interpreter
from sjtu_learning_assistant.external_mcp import ExternalMCP, MCPClientError
from sjtu_learning_assistant.agent_runtime.presets import PresetLoader
from sjtu_learning_assistant.agent_runtime.tools import ReadOnlyToolRegistry, AgentToolError
from sqlalchemy import create_engine


class SkillPackageTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.store=SkillPackages(self.root/'agent_skills.json',Path(__file__).resolve().parents[1])
    def package(self, files=None):
        path=self.root/'skill.zip'
        with zipfile.ZipFile(path,'w') as z:
            for name,body in (files or {'example/skill/SKILL.md':'---\nname: test-example\ndescription: 测试原结构\n---\n按原文执行。','example/README.md':'说明','example/scripts/demo.py':'from pathlib import Path\nPath("output.txt").write_text("ok")\nprint("script ok")'}).items():z.writestr(name,body)
        return path
    def test_original_structure_and_frontmatter_and_disabled(self):
        row=self.store.import_zip(self.package())
        self.assertEqual('skill/SKILL.md',row['entry']);self.assertIn('README.md',row['files']);self.assertIn('scripts/demo.py',row['files'])
        self.assertTrue(self.store.read(row['id'],row['entry'])['text'].startswith('---\nname:'))
        self.store.update(row['id'],enabled=False)
        with self.assertRaises(SkillPackageError):self.store.selected([row['id']])
        self.assertEqual('说明',self.store.read(row['id'],'README.md',require_enabled=False)['text'])
    def test_imported_package_missing_is_not_instruction_fallback(self):
        row=self.store.import_zip(self.package())
        import shutil
        shutil.rmtree(self.store.packages/row['id'])
        with self.assertRaises(SkillPackageError):self.store.read(row['id'],'README.md')

    def test_zip_traversal_and_symlink_rejected(self):
        with self.assertRaises(SkillPackageError):self.store.import_zip(self.package({'../SKILL.md':'evil'}))
        with zipfile.ZipFile(self.root/'link.zip','w') as z:
            i=zipfile.ZipInfo('SKILL.md');i.external_attr=(stat.S_IFLNK|0o777)<<16;z.writestr(i,'/tmp/file')
        with self.assertRaises(SkillPackageError):self.store.import_zip(self.root/'link.zip')
    def test_windows_reserved_zip_paths_fail_before_extraction(self):
        for name in ('CON.txt', 'aux', 'lpt1.md', 'COM¹.txt', 'trailing.', 'space ', 'a?b.md'):
            with self.subTest(name=name), self.assertRaisesRegex(SkillPackageError, 'Windows'):
                self.store.import_zip(self.package({'SKILL.md': '说明', 'scripts/'+name: 'example'}))
        self.assertFalse(self.store.packages.exists())
    def test_windows_python_and_shell_dispatch(self):
        with patch('sjtu_learning_assistant.skill_packages.os.name', 'nt'), patch('sjtu_learning_assistant.skill_packages.command_path', return_value='python.exe') as locate:
            self.assertEqual('python.exe', script_interpreter('.py'))
            locate.assert_called_once_with('python')
            locate.reset_mock()
            with self.assertRaisesRegex(SkillPackageError, 'Windows'):
                script_interpreter('.sh')
            locate.assert_not_called()
    def test_script_requires_explicit_flag_then_outputs_files(self):
        row=self.store.import_zip(self.package())
        with self.assertRaises(SkillPackageError):self.store.run_script(row['id'],'scripts/demo.py',[])
        self.store.update(row['id'],allow_scripts=True)
        out=self.store.run_script(row['id'],'scripts/demo.py',[])
        self.assertEqual(0,out['exit_code']);self.assertEqual(['output.txt'],out['files']);self.assertIn('script ok',out['text'])
    def test_only_two_agents_and_library_isolation(self):
        _,presets=PresetLoader().load();self.assertEqual({'general','knowledge'},set(presets))
        tools=set(presets['knowledge'].allowed_tools)
        self.assertNotIn('run_skill_script',tools);self.assertFalse(any(t.startswith('native_calendar') for t in tools))
        self.assertNotIn('propose_file_change',tools)
        self.assertEqual('general',PresetLoader().get('review-planner').id)


class ExternalMCPTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.store=ExternalMCP(self.root/'mcp.json')
        script=self.root/'server.py'
        script.write_text('from mcp.server.fastmcp import FastMCP\nfrom mcp.types import ToolAnnotations\nm=FastMCP("fixture")\n@m.tool(annotations=ToolAnnotations(readOnlyHint=True))\ndef search(query:str,max_results:int=3)->str:return "fixture:"+query\nm.run()\n')
        self.store.import_config({'mcpServers':{'fixture':{'command':sys.executable,'args':[str(script)]}}})
    def test_real_stdio_discovery_call_and_revocation(self):
        data=self.store.connect('fixture');tool=data['items'][0]['tools'][0]
        self.assertTrue(tool['read_only']);self.assertEqual(1,len(self.store.definitions()))
        result,_=self.store.call(tool['model_name'],{'query':'hello'})
        self.assertIn('fixture:hello',result['text'])
        self.store.update('fixture',enabled=False)
        self.assertEqual([],self.store.definitions())
        with self.assertRaises(MCPClientError):self.store.call(tool['model_name'],{'query':'hello'})
    def test_import_preserves_standard_env_without_returning_secret(self):
        self.store.import_config({'mcpServers':{'secret-service':{'command':'program','args':[],'env':{'TOKEN':'fixture-only'}}}})
        self.assertEqual('fixture-only',self.store.load()['secret-service']['config']['env']['TOKEN'])
        self.assertNotIn('fixture-only',json.dumps(self.store.list()))
    def test_bad_json_and_remote_http_rejected(self):
        with self.assertRaises(MCPClientError):self.store.import_config('{')
        with self.assertRaises(MCPClientError):self.store.import_config({'mcpServers':{'x':{'url':'http://example.com/mcp'}}})
    def test_registry_external_tools_and_no_permission_escalation(self):
        self.store.connect('fixture');engine=create_engine('sqlite://');self.addCleanup(engine.dispose)
        registry=ReadOnlyToolRegistry(engine,external_mcp=self.store)
        name=self.store.definitions()[0]['function']['name']
        self.assertTrue(any(d['function']['name']==name for d in registry.definitions((name,))))
        with self.assertRaises(AgentToolError):registry.execute(name,{'query':'hello'},PresetLoader().get('knowledge').allowed_tools)
