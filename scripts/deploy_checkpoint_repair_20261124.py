"""本轮独立部署状态与不可覆盖的旧包备份；复用安全替换流程。"""
from pathlib import Path
import deploy_ui_update_local as deploy

deploy.STATE = Path('/tmp/sjtu-checkpoint-repair-deploy-20261124.json')
deploy.BACKUP = Path.home() / 'Applications/SJTU App Backups/SJTU Learning Assistant 1.8.18 before checkpoint repair 20261124.app'
deploy.STAGED = Path('/Applications/SJTU Learning Assistant checkpoint repair 20261124 staging.app')

if __name__ == '__main__':
    deploy.main()
