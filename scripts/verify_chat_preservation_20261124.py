"""本轮独立只读快照，不重跑旧 before，不修改知识库。"""
from pathlib import Path
import verify_checkpoint_repair_20261124 as verify

verify.SNAPSHOT = Path('/tmp/sjtu-chat-preservation-20261124.json')

if __name__ == '__main__':
    verify.main()
