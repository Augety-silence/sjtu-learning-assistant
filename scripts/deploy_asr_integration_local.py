"""AI 配置合并及 ASR 钥匙串修复的独立可回退部署状态。"""
from pathlib import Path
import deploy_ui_update_local as deploy

deploy.STATE = Path('/tmp/sjtu-asr-integration-deploy-20261123.json')
deploy.BACKUP = Path.home() / 'Applications/SJTU App Backups/SJTU Learning Assistant 1.8.18 before ASR integration 20261123.app'
deploy.STAGED = Path('/Applications/SJTU Learning Assistant ASR integration 20261123 staging.app')

if __name__ == '__main__':
    deploy.main()
