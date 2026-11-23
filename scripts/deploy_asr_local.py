"""独立语音配置的可回退本机安装入口，不覆盖之前部署记录。"""
from pathlib import Path
import deploy_ui_update_local as deploy

deploy.STATE = Path('/tmp/sjtu-asr-deploy-20261123.json')
deploy.BACKUP = Path.home() / 'Applications/SJTU App Backups/SJTU Learning Assistant 1.8.18 before ASR 20261123.app'
deploy.STAGED = Path('/Applications/SJTU Learning Assistant ASR 20261123 staging.app')

if __name__ == '__main__':
    deploy.main()
