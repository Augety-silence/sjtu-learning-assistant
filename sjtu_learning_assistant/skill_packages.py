"""保留标准 SKILL.md / README / scripts 结构的本地技能包。"""
from __future__ import annotations

import hashlib
import json
import os
import signal
import stat
import subprocess
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from uuid import uuid4

from .external_mcp import LOCK, command_path, write_json

BUILTINS = (
    ('course-navigator','课程导航'), ('material-manager','资料整理'),
    ('deadline-coach','截止规划'), ('message-analyst','消息分析'),
    ('review-planner','复习规划'), ('course-summarizer','课程总结'),
    ('learning-diagnostics','学习诊断'), ('file-assistant','文件编辑'),
)


def portable_zip_path(path: PurePosixPath) -> bool:
    """Reject names that Windows aliases or cannot create, on every platform."""
    reserved = {'CON', 'PRN', 'AUX', 'NUL', 'CONIN$', 'CONOUT$',
                *(f'COM{i}' for i in range(1, 10)), *(f'LPT{i}' for i in range(1, 10)),
                'COM¹', 'COM²', 'COM³', 'LPT¹', 'LPT²', 'LPT³'}
    return all(part and not part.endswith((' ', '.'))
               and not any(ord(c) < 32 or c in '<>:"|?*' for c in part)
               and part.split('.')[0].upper() not in reserved
               for part in path.parts)


def script_interpreter(suffix: str) -> str:
    if os.name == 'nt' and suffix in ('.sh', '.bash'):
        raise SkillPackageError('Windows 不直接执行 Shell 技能；请使用 Python/JavaScript 版本，或单独配置 WSL MCP 服务。')
    command = {'.py': 'python' if os.name == 'nt' else 'python3', '.js': 'node',
               '.mjs': 'node', '.sh': '/bin/sh', '.bash': '/bin/bash'}.get(suffix)
    if not command:
        raise SkillPackageError('支持 Python、JavaScript 和 Shell 脚本。')
    return command_path(command)


class SkillPackageError(RuntimeError):
    pass


class SkillPackages:
    def __init__(self, path: Path, resource_root: Path):
        self.path=path
        self.packages=path.parent/'skill-packages'
        self.resource_root=resource_root

    def load(self):
        try:
            data=json.loads(self.path.read_text(encoding='utf-8'))
            if type(data) is not dict or type(data.get('skills',[])) is not list:
                raise ValueError()
            return data
        except FileNotFoundError:
            return {'version':2,'skills':[],'builtin_states':{}}
        except (ValueError,OSError):
            raise SkillPackageError('技能配置无法读取，请恢复配置文件后重试。') from None

    def list(self):
        data=self.load()
        result=[]
        for identity,label in BUILTINS:
            path=self.resource_root/'agent_presets/skills'/(identity+'.md')
            if not path.is_file():continue
            instructions=path.read_text(encoding='utf-8')
            result.append(dict(id='builtin-'+identity,name=label,description=instructions.splitlines()[0].lstrip('# ')[:160],
                               source='builtin',enabled=data.get('builtin_states',{}).get(identity,True),
                               files=['SKILL.md'],entry='SKILL.md',allow_scripts=False,instructions=instructions))
        for item in data.get('skills',[]):
            result.append(dict(item,source='user',enabled=item.get('enabled',True),allow_scripts=item.get('allow_scripts',False),
                               files=item.get('files') or ['SKILL.md'],entry=item.get('entry') or 'SKILL.md'))
        return result

    def get(self, identity, *, enabled=False):
        item=next((i for i in self.list() if i['id']==identity),None)
        if item is None or enabled and not item['enabled']:
            raise SkillPackageError('技能不存在或已关闭。')
        return item

    def update(self, identity, *, enabled=None, allow_scripts=None, delete=False):
        item=self.get(identity)
        with LOCK:
            data=self.load()
            if item['source']=='builtin':
                if delete or allow_scripts is not None:raise SkillPackageError('内置技能不能删除或执行外部脚本。')
                if type(enabled) is not bool:raise SkillPackageError('技能状态无效。')
                data.setdefault('builtin_states',{})[identity.removeprefix('builtin-')]=enabled
            else:
                row=next(i for i in data['skills'] if i['id']==identity)
                if delete:
                    # 元数据移除即可撤销使用；原包保留，便于恢复和追溯。
                    data['skills']=[i for i in data['skills'] if i['id']!=identity]
                else:
                    for key,value in (('enabled',enabled),('allow_scripts',allow_scripts)):
                        if value is not None:
                            if type(value) is not bool:raise SkillPackageError('技能状态无效。')
                            row[key]=value
            write_json(self.path,data)
        return self.list()

    def save_draft(self, raw):
        from .agent_runtime.presets import validate_user_skill
        row=validate_user_skill(raw)
        with LOCK:
            data=self.load()
            existing=next((i for i in data['skills'] if i['id']==row['id']),None)
            if raw.get('id') and not existing:raise SkillPackageError('技能不存在。')
            if existing:
                existing.update({k:v for k,v in row.items() if k!='id'})
            else:
                if len(data['skills'])>=32:raise SkillPackageError('外部技能最多32个。')
                data['skills'].append(dict(row,source='user',enabled=True,files=[],allow_scripts=False))
            write_json(self.path,data)
        return row

    def import_zip(self, source: Path):
        import yaml
        if not source.is_file() or source.is_symlink() or source.suffix.lower()!='.zip' or source.stat().st_size>32*1024*1024:
            raise SkillPackageError('请选择不超过32MB的普通 ZIP 技能包。')
        try:
            with zipfile.ZipFile(source) as archive:
                entries=[];seen=set();total=0
                for info in archive.infolist():
                    path=PurePosixPath(info.filename)
                    if info.is_dir():continue
                    if '__MACOSX' in path.parts or path.name=='.DS_Store':continue
                    if path.is_absolute() or '\\' in info.filename or any(p in ('..','.') for p in path.parts) or ':' in info.filename or '\0' in info.filename or stat.S_ISLNK(info.external_attr>>16):
                        raise SkillPackageError('ZIP 包含不安全路径或符号链接。')
                    if not portable_zip_path(path):
                        raise SkillPackageError('ZIP 文件名不兼容 Windows，请移除保留名称、尾随点/空格和特殊字符。')
                    if info.flag_bits&1:raise SkillPackageError('请使用未加密 ZIP 包。')
                    if path.as_posix().casefold() in seen:raise SkillPackageError('ZIP 包含重复文件名。')
                    seen.add(path.as_posix().casefold());total+=info.file_size
                    if total>64*1024*1024 or info.file_size>20*1024*1024 or len(entries)>=2000:
                        raise SkillPackageError('技能解压后不能超过64MB或2000个文件。')
                    if any(p in ('.git','node_modules','.venv') for p in path.parts):continue
                    entries.append((info,path))
                skill_files=[e for e in entries if e[1].name.casefold()=='skill.md']
                if len(skill_files)!=1:raise SkillPackageError('每个 ZIP 需要一个 SKILL.md；多个技能请分别导入。')
                info,entry=skill_files[0]
                if info.file_size>80000:raise SkillPackageError('SKILL.md 不能超过80KB。')
                body=archive.read(info).decode('utf-8-sig')
                if len(body)>20000:raise SkillPackageError('技能说明不能超过20000字。')
                metadata={}
                if body.startswith('---'):
                    parts=body.split('---',2)
                    if len(parts)!=3:raise SkillPackageError('SKILL.md YAML 头部未闭合。')
                    metadata=yaml.safe_load(parts[1]) or {}
                    if type(metadata) is not dict:raise SkillPackageError('技能 YAML 头部需要为键值对象。')
                name=metadata.get('name',entry.parent.name or source.stem)
                description=metadata.get('description','')
                if type(name) is not str or not name.strip() or len(name)>80 or type(description) is not str or len(description)>2000:
                    raise SkillPackageError('技能名称或 description 格式无效。')
                prefixes={p.parts[0] for _,p in entries if len(p.parts)>1}
                strip_first=len(prefixes)==1 and all(len(p.parts)>1 for _,p in entries)
                relative=lambda p:PurePosixPath(*p.parts[1:]) if strip_first else p
                identity='s-'+uuid4().hex[:12]
                destination=self.packages/identity
                destination.mkdir(parents=True,exist_ok=False)
                files=[]
                try:
                    for info,path in entries:
                        rel=relative(path);target=destination/str(rel)
                        target.parent.mkdir(parents=True,exist_ok=True)
                        target.write_bytes(archive.read(info));files.append(str(rel))
                    row=dict(id=identity,name=name.strip(),description=description.strip(),instructions=body,source='user',
                             enabled=True,allow_scripts=False,entry=str(relative(entry)),files=files,
                             sha256=hashlib.sha256(source.read_bytes()).hexdigest(),allowed_tools=[],reference_attachment_ids=[])
                    with LOCK:
                        data=self.load()
                        if len(data['skills'])>=32:raise SkillPackageError('外部技能最多32个。')
                        data['skills'].append(row);write_json(self.path,data)
                    return row
                except BaseException:
                    import shutil
                    shutil.rmtree(destination)
                    raise
        except (zipfile.BadZipFile,UnicodeError,ValueError,yaml.YAMLError):
            raise SkillPackageError('ZIP 或 SKILL.md 格式无效，请核对 UTF-8 文件与 YAML。') from None

    def selected(self, identities):
        if identities is None:return []
        if type(identities) is not list or len(identities)>8 or any(type(i) is not str for i in identities):
            raise SkillPackageError('最多选择8个已启用技能。')
        return [self.get(i,enabled=True) for i in dict.fromkeys(identities)]

    def read(self, identity, path, offset=0, *, require_enabled=True):
        row=self.get(identity,enabled=require_enabled)
        if type(path) is not str or path not in row['files'] or type(offset) is not int or not 0<=offset<=200000:
            raise SkillPackageError('技能文件或读取位置无效。')
        if row['source']=='builtin' or not row.get('sha256') and row['files']==['SKILL.md']:text=row['instructions']
        else:
            source=self._package_file(identity,path)
            if source.stat().st_size>1024*1024:
                raise SkillPackageError('技能文件不能读取。')
            try:text=source.read_text(encoding='utf-8')
            except UnicodeError:raise SkillPackageError('此文件为二进制资源，不能作为文本读取。') from None
        return {'text':text[offset:offset+8000],'next_offset':offset+8000 if len(text)>offset+8000 else None}

    def _package_file(self, identity, path):
        base=self.packages/identity
        candidate=base
        if base.is_symlink():raise SkillPackageError('技能目录不能是符号链接。')
        for part in PurePosixPath(path).parts:
            candidate=candidate/part
            if candidate.is_symlink():raise SkillPackageError('技能文件不能是符号链接。')
        try:resolved=candidate.resolve(strict=True)
        except OSError:raise SkillPackageError('技能文件不存在或不可读取。') from None
        if not resolved.is_relative_to(base.resolve()) or not resolved.is_file():
            raise SkillPackageError('技能文件路径无效。')
        return resolved

    def run_script(self, identity, path, args):
        row=self.get(identity,enabled=True)
        if not row.get('allow_scripts'):raise SkillPackageError('该技能尚未启用脚本执行，请在技能详情中设置。')
        if type(path) is not str or path not in row['files'] or 'scripts' not in PurePosixPath(path).parts:
            raise SkillPackageError('只能运行此技能 scripts 文件夹中已登记的脚本。')
        if type(args) is not list or len(args)>24 or any(type(a) is not str or len(a)>2000 or '\0' in a for a in args):
            raise SkillPackageError('脚本参数无效。')
        script=self._package_file(identity,path)
        interpreter=script_interpreter(script.suffix.lower())
        run_id=uuid4().hex
        output=self.path.parent/'skill-output'/run_id;output.mkdir(parents=True)
        with tempfile.TemporaryFile() as stdout,tempfile.TemporaryFile() as stderr:
            process=subprocess.Popen([interpreter,str(script),*args],cwd=output,stdout=stdout,stderr=stderr,
                                     start_new_session=os.name!='nt',env=dict(os.environ,SJTU_SKILL_ROOT=str(script.parent.parent),SJTU_SKILL_OUTPUT=str(output)))
            try:process.wait(timeout=45)
            except subprocess.TimeoutExpired:
                if os.name=='nt':subprocess.run(['taskkill','/PID',str(process.pid),'/T','/F'],capture_output=True)
                else:os.killpg(process.pid,signal.SIGKILL)
                process.wait();raise SkillPackageError('脚本执行超过45秒，已停止。') from None
            stdout.seek(0);stderr.seek(0)
            files=[str(p.relative_to(output)) for p in output.rglob('*') if p.is_file() and not p.is_symlink()][:100]
            return dict(run_id=run_id,exit_code=process.returncode,text=stdout.read(16000).decode('utf-8',errors='replace'),
                        error=stderr.read(4000).decode('utf-8',errors='replace') if process.returncode else '',files=files)
