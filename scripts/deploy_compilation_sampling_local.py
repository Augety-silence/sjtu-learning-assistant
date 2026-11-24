"""本机编译抽样与缓存更新；暂停派发、排空、可回退替换，保留原文与检查点。"""
from pathlib import Path
import deploy_ui_update_local as deploy

deploy.STATE = Path('/tmp/sjtu-compilation-sampling-deploy-20261123.json')
deploy.BACKUP = Path.home() / 'Applications/SJTU App Backups/SJTU Learning Assistant 1.8.18 before compilation sampling 20261123.app'
deploy.STAGED = Path('/Applications/SJTU Learning Assistant compilation sampling 20261123 staging.app')

if __name__ == '__main__':
    deploy.main()
