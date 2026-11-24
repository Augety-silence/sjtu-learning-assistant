from pathlib import Path
import deploy_ui_update_local as deploy

deploy.STATE = Path('/tmp/sjtu-oversize-asr-deploy-20261124.json')
deploy.BACKUP = Path.home() / 'Applications/SJTU App Backups/SJTU Learning Assistant 1.8.18 before oversize skip and asr chunking 20261124.app'
deploy.STAGED = Path('/Applications/SJTU Learning Assistant oversize asr 20261124 staging.app')

if __name__ == '__main__':
    deploy.main()
