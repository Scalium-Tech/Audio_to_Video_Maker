import React from "react";
import {
    AbsoluteFill,
    Audio,
    Img,
    interpolate,
    useCurrentFrame,
    useVideoConfig,
} from "remotion";

interface WordTimestamp {
    word: string;
    start: number;
    end: number;
}

interface LyricLine {
    text: string;
    start: number;
    end: number;
    words?: WordTimestamp[];
}

export interface LyricVideoProps {
    audioSrc: string;
    lyrics: LyricLine[];
    backgroundImage?: string;
}

export const LyricVideo: React.FC<LyricVideoProps> = ({ audioSrc, lyrics, backgroundImage }) => {
    const frame = useCurrentFrame();
    const { fps } = useVideoConfig();
    const currentTime = frame / fps;

    // Find the currently active lyric
    const activeLyric = lyrics.find(
        (l) => currentTime >= l.start && currentTime <= l.end
    );

    // Slow Ken Burns zoom for background image (very render-friendly)
    const bgScale = 1 + (frame * 0.00008);

    return (
        <AbsoluteFill
            style={{
                background: `linear-gradient(${135 + Math.sin(frame * 0.003) * 10}deg, hsl(${250 + Math.sin(frame * 0.005) * 15}, 40%, 8%) 0%, hsl(${230 + Math.cos(frame * 0.004) * 12}, 35%, 12%) 40%, hsl(${210 + Math.sin(frame * 0.006) * 10}, 38%, 14%) 100%)`,
                justifyContent: "center",
                alignItems: "center",
            }}
        >
            {/* Audio */}
            <Audio src={audioSrc} />

            {/* Background Image (if provided) */}
            {backgroundImage && (
                <>
                    <Img
                        src={backgroundImage}
                        style={{
                            position: "absolute",
                            top: 0,
                            left: 0,
                            width: "100%",
                            height: "100%",
                            objectFit: "cover",
                            transform: `scale(${bgScale})`,
                            zIndex: 0,
                        }}
                    />
                    {/* Dark overlay so lyrics stay readable */}
                    <div
                        style={{
                            position: "absolute",
                            top: 0,
                            left: 0,
                            width: "100%",
                            height: "100%",
                            background: "rgba(0, 0, 0, 0.40)",
                            zIndex: 1,
                        }}
                    />
                </>
            )}

            {/* ═══════════════════════════════════════════════════════════
                 Background: Pulsing Soundwave Rings
                 ═══════════════════════════════════════════════════════════ */}
            {[0, 1, 2, 3, 4].map((ringIdx) => {
                const ringCycle = 150;
                const offset = ringIdx * 30;
                const ringProgress = ((frame + offset) % ringCycle) / ringCycle;
                const ringScale = 0.2 + ringProgress * 2.2;
                const ringOpacity = Math.max(0, 0.5 - ringProgress * 0.55);
                return (
                    <div
                        key={`ring-${ringIdx}`}
                        style={{
                            position: "absolute",
                            top: "50%",
                            left: "50%",
                            width: 500,
                            height: 500,
                            borderRadius: "50%",
                            border: `3px solid rgba(139, 92, 246, ${0.6 - ringProgress * 0.5})`,
                            transform: `translate(-50%, -50%) scale(${ringScale})`,
                            opacity: ringOpacity,
                            zIndex: 2,
                        }}
                    />
                );
            })}

            {/* ═══════════════════════════════════════════════════════════
                 Flashy Light Bursts (no blur, render-friendly)
                 ═══════════════════════════════════════════════════════════ */}
            {[0, 1, 2, 3].map((i) => {
                const cycle = 70 + i * 20;
                const progress = ((frame + i * 30) % cycle) / cycle;
                const window = 0.15;
                const active = progress < window;
                const opacity = active
                    ? progress < window / 2
                        ? (progress / (window / 2)) * 0.7
                        : ((window - progress) / (window / 2)) * 0.7
                    : 0;
                const scale = active ? 0.5 + (progress / window) * 2.5 : 0;
                const positions = [
                    { x: 12, y: 18 }, { x: 85, y: 25 },
                    { x: 18, y: 78 }, { x: 80, y: 72 },
                ];
                const p = positions[i];
                return (
                    <div
                        key={`flash-${i}`}
                        style={{
                            position: "absolute",
                            left: `${p.x}%`,
                            top: `${p.y}%`,
                            width: 250,
                            height: 250,
                            borderRadius: "50%",
                            background: "radial-gradient(circle, rgba(255,255,255,0.8) 0%, rgba(255, 255, 255, 0.29) 35%, transparent 65%)",
                            transform: `translate(-50%,-50%) scale(${scale})`,
                            opacity,
                            zIndex: 3,
                            pointerEvents: "none" as const,
                        }}
                    />
                );
            })}

            {/* ═══════════════════════════════════════════════════════════
                 Sparkle Dots (tiny twinkling points)
                 ═══════════════════════════════════════════════════════════ */}
            {Array.from({ length: 8 }).map((_, i) => {
                const seed = (i + 1) * 13.7;
                const sx = ((seed * 7.3) % 90) + 5;
                const sy = ((seed * 11.1) % 85) + 7;
                const twinkle = 0.15 + Math.pow(Math.sin(frame * (0.05 + i * 0.012) + i * 2.3), 2) * 0.85;
                const size = 3 + (i % 3);
                const colors = ["#c4b5fd", "#93c5fd", "#f9a8d4", "#fde68a"];
                return (
                    <div
                        key={`sparkle-${i}`}
                        style={{
                            position: "absolute",
                            left: `${sx}%`,
                            top: `${sy}%`,
                            width: size,
                            height: size,
                            borderRadius: "50%",
                            backgroundColor: colors[i % 4],
                            opacity: twinkle,
                            zIndex: 3,
                        }}
                    />
                );
            })}

            {/* ═══════════════════════════════════════════════════════════
                 LAYER 5: Lyrics (always on top)
                 ═══════════════════════════════════════════════════════════ */}
            <div
                style={{
                    position: "absolute",
                    top: 0,
                    left: 0,
                    right: 0,
                    bottom: 0,
                    display: "flex",
                    justifyContent: "center",
                    alignItems: "center",
                    zIndex: 10,
                }}
            >
                {activeLyric && (
                    <LyricDisplay
                        key={`lyric-${activeLyric.start}-${activeLyric.end}`}
                        text={activeLyric.text}
                        words={activeLyric.words}
                        startFrame={activeLyric.start * fps}
                        endFrame={activeLyric.end * fps}
                        currentFrame={frame}
                        currentTime={currentTime}
                        fps={fps}
                    />
                )}
            </div>

            {/* Song title at top (subtle) */}
            <div
                style={{
                    position: "absolute",
                    top: 40,
                    left: 0,
                    right: 0,
                    textAlign: "center",
                    fontFamily: "'Noto Sans Devanagari', 'Mangal', 'Kokila', sans-serif",
                    fontSize: 18,
                    color: "rgba(255, 255, 255, 0.3)",
                    letterSpacing: 4,
                    textTransform: "uppercase",
                    zIndex: 11,
                }}
            >
                ♪ Now Playing
            </div>

            {/* Progress bar at bottom */}
            <div
                style={{
                    position: "absolute",
                    bottom: 0,
                    left: 0,
                    height: 4,
                    background: "linear-gradient(90deg, #6366f1, #a855f7, #ec4899)",
                    width: `${(currentTime / (lyrics[lyrics.length - 1]?.end || 1)) * 100}%`,
                    borderRadius: 2,
                    boxShadow: "0 0 12px rgba(99, 102, 241, 0.6)",
                    zIndex: 11,
                }}
            />
        </AbsoluteFill>
    );
};


// ─── Karaoke-style Smooth Sweep LyricDisplay ────────────────────────────────
const FONT_SIZE = 44;


const LyricDisplay: React.FC<{
    text: string;
    words?: WordTimestamp[];
    startFrame: number;
    endFrame: number;
    currentFrame: number;
    currentTime: number;
    fps: number;
}> = ({ text, words, startFrame, endFrame, currentFrame, currentTime }) => {
    const totalFrames = endFrame - startFrame;
    const safeFade = Math.min(8, Math.floor(totalFrames / 3));

    // Overall fade in/out
    const blockOpacity = interpolate(
        currentFrame,
        [startFrame, startFrame + safeFade, endFrame - safeFade, endFrame],
        [0, 1, 1, 0],
        { extrapolateLeft: "clamp", extrapolateRight: "clamp" }
    );

    const blockTranslateY = interpolate(
        currentFrame,
        [startFrame, startFrame + safeFade],
        [20, 0],
        { extrapolateLeft: "clamp", extrapolateRight: "clamp" }
    );

    // Use word timestamps if available, otherwise split text
    const displayWords = words && words.length > 0
        ? words
        : text.split(/\s+/).filter(Boolean).map((w, i, arr) => {
            const segStart = startFrame / 30;
            const segEnd = endFrame / 30;
            const slot = (segEnd - segStart) / arr.length;
            return { word: w, start: segStart + i * slot, end: segStart + (i + 1) * slot };
        });

    return (
        <div
            style={{
                opacity: blockOpacity,
                transform: `translateY(${blockTranslateY}px)`,
                fontFamily:
                    "'Noto Sans Devanagari', 'Mangal', 'Kokila', sans-serif",
                fontSize: FONT_SIZE,
                fontWeight: 700,
                textAlign: "center",
                maxWidth: "85%",
                lineHeight: 1.6,
                letterSpacing: 1,
                maxHeight: Math.round(FONT_SIZE * 1.6 * 2),
                overflow: "hidden" as const,
            }}
        >
            {displayWords.map((w, idx) => {
                const isActive = currentTime >= w.start && currentTime <= w.end;
                const isPast = currentTime > w.end;

                let color = "#ffffff";
                let textShadow = "0 0 40px rgba(99, 102, 241, 0.5), 0 4px 20px rgba(0,0,0,0.5)";

                if (isPast) {
                    color = "rgba(255, 100, 100, 0.85)";
                    textShadow = "0 0 20px rgba(255, 59, 59, 0.3), 0 4px 20px rgba(0,0,0,0.5)";
                }
                if (isActive) {
                    color = "rgb(255, 80, 80)";
                    textShadow = "0 0 30px rgba(255, 59, 59, 0.6), 0 0 60px rgba(255, 59, 59, 0.3), 0 4px 20px rgba(0,0,0,0.5)";

                }

                return (
                    <React.Fragment key={`w-${idx}`}>
                        <span
                            style={{
                                color,
                                textShadow,
                                display: "inline-block",
                                marginRight: "0.4em",
                            }}
                        >
                            {w.word}{" "}
                        </span>
                        {(w.word.endsWith(",") || w.word.endsWith("!") || w.word.endsWith("।")) && <br />}
                    </React.Fragment>
                );
            })}
        </div>
    );
};


