"""从精确本地提交在独立临时目录打包；不清理旧build/dist，不安装新依赖。"""
import json
import os
from pathlib import Path
import subprocess
import tempfile

repo = Path(__file__).resolve().parents[1]
build = Path(tempfile.mkdtemp(prefix='sjtu-auto-recovery-build-')).resolve()
python = repo / '.venv/bin/python'
revision = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=repo, text=True).strip()
archive = subprocess.check_output(['git', 'archive', revision], cwd=repo)
subprocess.run(['tar', '-x', '-C', str(build)], input=archive, check=True)
os.symlink(repo / 'dashboard-web/node_modules', build / 'dashboard-web/node_modules')
env = dict(os.environ, PYTHONPATH=str(build))
for key in ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'http_proxy', 'https_proxy', 'all_proxy', 'NO_PROXY', 'no_proxy'):
    env.pop(key, None)
def run(command):
    subprocess.run([str(arg) for arg in command], cwd=build, env=env, check=True)
print(json.dumps({'build': str(build), 'revision': revision}), flush=True)
run([python, 'scripts/generate_macos_icon.py'])
run(['npm', '--prefix', 'dashboard-web', 'run', 'build'])
run([python, '-m', 'PyInstaller', '--noconfirm', '--workpath', build / 'build', '--distpath', build / 'dist', 'packaging/desktop.spec'])
bundle = build / 'dist/SJTU Learning Assistant.app'
run(['/usr/bin/xattr', '-cr', bundle])
run(['/usr/bin/codesign', '--force', '--deep', '--sign', '-', bundle])
run(['/usr/bin/codesign', '--verify', '--deep', '--strict', bundle])
run([python, 'scripts/verify_macos_bundle.py', bundle])
run(['/usr/bin/plutil', '-lint', bundle / 'Contents/Info.plist'])
print(json.dumps({'bundle': str(bundle), 'revision': revision, 'verified': True}), flush=True)
