"""本轮聊天即时补充与并行会话的独立部署，旧包路径禁止覆盖。"""
from pathlib import Path
import deploy_ui_update_local as deploy

deploy.STATE = Path('/tmp/sjtu-chat-steering-deploy-20261124.json')
deploy.BACKUP = Path.home() / 'Applications/SJTU App Backups/SJTU Learning Assistant 1.8.18 before chat steering 20261124.app'
deploy.STAGED = Path('/Applications/SJTU Learning Assistant chat steering 20261124 staging.app')

if __name__ == '__main__':
    deploy.main()
