import { useState, useRef, useEffect, useMemo, useCallback } from "react";
import { motion, AnimatePresence } from "framer-motion";

export type Beat = {
  time: number;
  duration: number;
  spotY: number;
  callout: string;
};

const FALLBACK_BEATS: Beat[] = [
  { time: 0,  duration: 6,  spotY: 5,  callout: "This is a working prototype — built autonomously for your company." },
  { time: 6,  duration: 7,  spotY: 28, callout: "We found this pain signal directly from your public careers page." },
  { time: 13, duration: 7,  spotY: 55, callout: "Here's exactly how the product solves each problem you posted about." },
  { time: 20, duration: 8,  spotY: 82, callout: "One click starts a 30-day paid pilot. No meetings required." },
];

type Props = {
  audioUrl?: string;
  beats?: Beat[];
  companyName?: string;
};

export function CinematicPitchPlayer({ audioUrl, beats, companyName = "Target" }: Props) {
  const b = beats?.length ? beats : FALLBACK_BEATS;
  const audioRef = useRef<HTMLAudioElement>(null);
  const [playing, setPlaying] = useState(false);
  const [t, setT] = useState(0);
  const [dur, setDur] = useState(0);

  const maxT = useMemo(() => Math.max(...b.map((x) => x.time + x.duration)), [b]);

  const active = useMemo(
    () => b.find((x) => t >= x.time && t < x.time + x.duration) ?? null,
    [b, t],
  );

  // Sync with real <audio> when present
  useEffect(() => {
    const a = audioRef.current;
    if (!a) return;
    const onTime = () => setT(a.currentTime);
    const onMeta = () => setDur(a.duration);
    const onEnd = () => setPlaying(false);
    a.addEventListener("timeupdate", onTime);
    a.addEventListener("loadedmetadata", onMeta);
    a.addEventListener("ended", onEnd);
    return () => { a.removeEventListener("timeupdate", onTime); a.removeEventListener("loadedmetadata", onMeta); a.removeEventListener("ended", onEnd); };
  }, []);

  // Simulated clock when no audio
  useEffect(() => {
    if (audioUrl || !playing) return;
    const iv = setInterval(() => {
      setT((prev) => {
        if (prev >= maxT) { setPlaying(false); return 0; }
        return prev + 0.1;
      });
    }, 100);
    return () => clearInterval(iv);
  }, [audioUrl, playing, maxT]);

  const toggle = useCallback(() => {
    const a = audioRef.current;
    if (a) { playing ? a.pause() : a.play(); }
    setPlaying((p) => !p);
  }, [playing]);

  const pct = (dur || maxT) ? Math.min(100, (t / (dur || maxT)) * 100) : 0;

  return (
    <div className="cp-root">
      {audioUrl && <audio ref={audioRef} src={audioUrl} preload="metadata" />}

      {/* header */}
      <div className="cp-hdr">
        <div className={`cp-avatar ${playing ? "cp-avatar-on" : ""}`}>
          <span>🤖</span>
        </div>
        <div>
          <div className="rv-mono cp-title">AI PRESENTATION</div>
          <div className="cp-sub">for {companyName}</div>
        </div>
      </div>

      {/* stage */}
      <div className="cp-stage">
        <AnimatePresence mode="wait">
          {playing && active ? (
            <motion.div
              key={active.callout}
              className="cp-beat"
              initial={{ opacity: 0, y: 12 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -12 }}
              transition={{ duration: 0.35 }}
            >
              {/* spotlight */}
              <motion.div
                className="cp-spot"
                animate={{ top: `${active.spotY}%` }}
                transition={{ type: "spring", stiffness: 60, damping: 18 }}
              />

              {/* cursor */}
              <motion.div
                className="cp-cursor"
                animate={{
                  top: `${active.spotY + 3}%`,
                  left: `${35 + Math.sin(t * 0.8) * 18}%`,
                }}
                transition={{ type: "spring", stiffness: 50, damping: 14 }}
              />

              {/* callout text */}
              <div className="cp-callout">{active.callout}</div>
            </motion.div>
          ) : !playing ? (
            <motion.button
              key="idle"
              className="cp-play"
              onClick={toggle}
              initial={{ opacity: 0, scale: 0.9 }}
              animate={{ opacity: 1, scale: 1 }}
              exit={{ opacity: 0, scale: 0.9 }}
              whileHover={{ scale: 1.05 }}
              whileTap={{ scale: 0.95 }}
            >
              <span className="cp-play-icon">▶</span>
              <span className="cp-play-txt">Play Presentation</span>
            </motion.button>
          ) : null}
        </AnimatePresence>
      </div>

      {/* controls */}
      <div className="cp-ctrl">
        <button className="cp-ctrl-btn" onClick={toggle}>{playing ? "⏸" : "▶"}</button>
        <div className="cp-bar">
          <motion.div className="cp-bar-fill" animate={{ width: `${pct}%` }} transition={{ duration: 0.15 }} />
        </div>
        <span className="rv-mono cp-time">{Math.floor(t)}s</span>
      </div>
    </div>
  );
}
