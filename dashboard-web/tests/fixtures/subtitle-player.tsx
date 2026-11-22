import { useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { syncCaptionTrack, VideoCaptions } from "@/components/VideoCaptions";
import "../../src/index.css";

function Player() {
  const ref = useRef<HTMLVideoElement>(null);
  const [sample, setSample] = useState<{ url: string; vtt: string; cues: { startMs: number; endMs: number; text: string }[] } | null>(null);
  const [subtitle, setSubtitle] = useState("");
  useEffect(() => {
    const load = async () => {
      const result = await window.pywebview.api.invoke("sample", {});
      if (result.ok) {
        setSample(result.data);
        setSubtitle(URL.createObjectURL(new Blob([result.data.vtt], { type: "text/vtt" })));
      }
    };
    if (window.pywebview?.api) void load();
    else window.addEventListener("pywebviewready", () => void load(), { once: true });
  }, []);
  return <main style={{ padding: 20 }}><div className="video-player-shell" style={{ height: 480 }}>
    {sample && <><video ref={ref} controls preload="metadata" src={sample.url}>
      <track kind="subtitles" src={subtitle} srcLang="zh" label="中文字幕" default onLoad={() => ref.current && syncCaptionTrack(ref.current)} />
    </video><VideoCaptions videoRef={ref} cues={sample.cues} /></>}
  </div><button type="button" onClick={() => { if (ref.current) { ref.current.muted = true; void ref.current.play(); } }}>播放</button></main>;
}
createRoot(document.getElementById("root")!).render(<Player />);
