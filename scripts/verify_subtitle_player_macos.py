"""真实录像的 WKWebView 字幕、跳转和倍速验收；只使用隔离字幕目录。"""
from __future__ import annotations

import argparse
import json
import threading
import time
from pathlib import Path

import webview
from verify_subtitle_prefetch_local import load_sample


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("html", type=Path)
    args = parser.parse_args()
    sample = load_sample()
    class Bridge:
        def invoke(self, action, payload=None):
            return {"ok": True, "data": sample} if action == "sample" else {"ok": False}
    window = webview.create_window("字幕播放隔离验收", args.html.resolve(strict=True).as_uri(),
                                   js_api=Bridge(), width=1050, height=760)
    outcome = {"ok": False}
    def wait_for(expression, seconds=20):
        deadline = time.monotonic() + seconds
        while not window.evaluate_js(expression):
            if time.monotonic() > deadline:
                raise RuntimeError("播放器验收等待超时")
            threading.Event().wait(0.1)
    def check():
        try:
            wait_for("Boolean(document.querySelector('video')?.readyState >= 1)")
            cues = sample["cues"]
            valid = [cue for cue in cues if cue["endMs"] - cue["startMs"] > 500]
            targets = valid[:2]
            assert len(targets) == 2
            for cue in targets:
                target = (cue["startMs"] + cue["endMs"]) / 2000
                window.evaluate_js(f"document.querySelector('video').pause(); document.querySelector('video').currentTime={target}")
                # Compare in-page, never emit subtitle text or playback credentials.
                expected = cue["text"].replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"').replace("&#39;", "'").replace("&amp;", "&")
                wait_for(f"document.querySelector('.video-caption-overlay')?.textContent === {json.dumps(expected)}")
            layout_checks = []
            for width in (1050, 760):
                window.resize(width, 760)
                threading.Event().wait(0.2)
                layout = window.evaluate_js("""(() => {
                  const player=document.querySelector('.video-player-shell').getBoundingClientRect();
                  const caption=document.querySelector('.video-caption-overlay').getBoundingClientRect();
                  return caption.left>=player.left && caption.right<=player.right && caption.bottom<=player.bottom-60;
                })()""")
                assert layout
                layout_checks.append(layout)
            window.evaluate_js("document.querySelector('video').playbackRate=2; document.querySelector('main>button').click()")
            before = window.evaluate_js("document.querySelector('video').currentTime")
            wait_for(f"document.querySelector('video').currentTime > {before + .3}")
            window.evaluate_js("document.querySelector('video').pause()")
            threading.Event().wait(0.2)
            rate = window.evaluate_js("document.querySelector('video').playbackRate")
            assert rate == 2
            window.evaluate_js("document.querySelector('.video-caption-toggle').click()")
            wait_for("!document.querySelector('.video-caption-overlay')")
            window.evaluate_js("document.querySelector('.video-caption-toggle').click()")
            tracks = window.evaluate_js("Array.from(document.querySelector('video').textTracks).map(t=>t.mode)")
            assert tracks and all(mode == "hidden" for mode in tracks)
            outcome.update(sample["report"], native="WKWebView", visible_captions=True,
                           seek_aligned=True, playback_rate=rate, layout_clear=all(layout_checks), native_track_ready=True)
        except Exception as exc:
            outcome.update(ok=False, error_type=type(exc).__name__)
        finally:
            print(json.dumps(outcome, ensure_ascii=False), flush=True)
            window.destroy()
    webview.start(check, gui="cocoa", debug=False)
    return 0 if outcome["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
