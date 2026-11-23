"""本轮本地热修部署，复用已验证的暂停/排空/签名/回退安装流程。"""
from pathlib import Path
import deploy_ui_update_local as deploy

deploy.STATE = Path('/tmp/sjtu-stall-recovery-deploy-20261123.json')
deploy.BACKUP = Path.home() / 'Applications/SJTU App Backups/SJTU Learning Assistant 1.8.18 before stall recovery 20261123.app'
deploy.STAGED = Path('/Applications/SJTU Learning Assistant stall recovery 20261123 staging.app')

if __name__ == '__main__':
    deploy.main()
