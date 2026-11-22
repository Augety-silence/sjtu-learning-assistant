"""只读查看本机断点续编与最近实际请求，不读密钥、不输出字幕或课件正文。"""
import argparse
import json
import sqlite3
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sjtu_learning_assistant.database import APP_SUPPORT_DIR
from sjtu_learning_assistant.local_settings import SettingsStore


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--since', type=float, default=0)
    args = parser.parse_args()
    source = Path(SettingsStore().load().archive_root)
    state = json.loads((source / 'Knowledge Markdown/99_System/Course-Sync.json').read_text())
    courses = Counter(item.get('status', 'unknown') for item in state['items'].values())
    subtitle_states, notes = Counter(), Counter()
    for directory in (APP_SUPPORT_DIR / 'transcripts/v1/videos').iterdir():
        path = directory / 'manifest.json'
        if directory.is_symlink() or path.is_symlink() or not path.is_file():
            continue
        value = json.loads(path.read_text())
        subtitle_states[value.get('status', 'unknown')] += 1
        notes[value.get('lesson_notes', {}).get('status', 'unknown')] += 1
    now = time.time()
    runtime = APP_SUPPORT_DIR / 'ai-runtime.sqlite3'
    with sqlite3.connect('file:' + quote(str(runtime)) + '?mode=ro', uri=True) as db:
        recent = db.execute('SELECT scope,started,ended,lease,outcome,input,output,cache FROM requests WHERE started>=? ORDER BY started',
                            (max(args.since, now - 3600),)).fetchall()
        inflight = db.execute('SELECT count(*) FROM requests WHERE started IS NOT NULL AND ended IS NULL AND lease>?', (now,)).fetchone()[0]
        queued = db.execute('SELECT count(*) FROM requests WHERE started IS NULL AND ended IS NULL AND lease>?', (now,)).fetchone()[0]
    per_scope, peak = {}, 0
    for scope, started, *_ in recent:
        window = [stamp for stamp in per_scope.get(scope, []) if stamp > started - 60]
        window.append(started)
        peak = max(peak, len(window))
        per_scope[scope] = window
    minute = [row for row in recent if row[1] > now - 60]
    print(json.dumps(dict(time=datetime.now().isoformat(timespec='seconds'), courses=dict(courses),
                          subtitle_states=dict(subtitle_states), lesson_summaries=dict(notes),
                          requests=dict(recent=len(recent), last_minute=len(minute), peak_rolling_minute=peak,
                                        inflight=inflight, queued=queued, outcomes=dict(Counter(row[4] for row in recent)),
                                        input_tokens=sum(row[5] or 0 for row in recent), output_tokens=sum(row[6] or 0 for row in recent),
                                        cached_tokens=sum(row[7] or 0 for row in recent))), ensure_ascii=False))


if __name__ == '__main__':
    main()
