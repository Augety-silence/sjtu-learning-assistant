"""封底分段补修的独立安全替换；不覆盖上一轮状态或任何旧包备份。"""
from pathlib import Path
import deploy_ui_update_local as deploy

deploy.STATE = Path('/tmp/sjtu-checkpoint-fragments-deploy-20261124.json')
deploy.BACKUP = Path.home() / 'Applications/SJTU App Backups/SJTU Learning Assistant 1.8.18 before short-fragment repair 20261124.app'
deploy.STAGED = Path('/Applications/SJTU Learning Assistant checkpoint fragments 20261124 staging.app')

if __name__ == '__main__':
    deploy.main()
