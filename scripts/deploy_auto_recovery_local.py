"""有界自动恢复本地热修：保留全部正文、响应与可回退App。"""
from pathlib import Path
import deploy_ui_update_local as deploy

deploy.STATE = Path('/tmp/sjtu-auto-recovery-deploy-20261123.json')
deploy.BACKUP = Path.home() / 'Applications/SJTU App Backups/SJTU Learning Assistant 1.8.18 before auto recovery 20261123.app'
deploy.STAGED = Path('/Applications/SJTU Learning Assistant auto recovery 20261123 staging.app')

if __name__ == '__main__':
    deploy.main()
