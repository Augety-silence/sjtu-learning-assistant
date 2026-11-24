"""封底复验补充包：沿用已暂停状态，另存未启动中间包，不覆盖任何备份。"""
from pathlib import Path
import deploy_ui_update_local as deploy

# 沿用同一暂停周期的原 AI 开关，避免暂停期间重新 capture 后丢失原启用状态。
deploy.STATE = Path('/tmp/sjtu-checkpoint-fragments-deploy-20261124.json')
deploy.BACKUP = Path.home() / 'Applications/SJTU App Backups/SJTU Learning Assistant 1.8.18 before final fragment validation 20261124.app'
deploy.STAGED = Path('/Applications/SJTU Learning Assistant checkpoint final 20261124 staging.app')

if __name__ == '__main__':
    deploy.main()
