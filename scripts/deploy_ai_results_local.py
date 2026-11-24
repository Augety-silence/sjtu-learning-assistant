"""本机 AI 最终答案与知识检索修复，独立、可回退安装入口。"""
from pathlib import Path
import deploy_ui_update_local as deploy

deploy.STATE = Path('/tmp/sjtu-ai-results-deploy-20261123.json')
deploy.BACKUP = Path.home() / 'Applications/SJTU App Backups/SJTU Learning Assistant 1.8.18 before AI results 20261123.app'
deploy.STAGED = Path('/Applications/SJTU Learning Assistant AI results 20261123 staging.app')

if __name__ == '__main__':
    deploy.main()
