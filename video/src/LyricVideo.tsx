import React from "react";
import {
    AbsoluteFill,
    Audio,
    interpolate,
    useCurrentFrame,
    useVideoConfig,
} from "remotion";

interface LyricLine {
    text: string;
    start: number;
    end: number;
}

export interface LyricVideoProps {
    audioSrc: string;
    lyrics: LyricLine[];
}

export const LyricVideo: React.FC<LyricVideoProps> = ({ audioSrc, lyrics }) => {
    const frame = useCurrentFrame();
    const { fps } = useVideoConfig();
    const currentTime = frame / fps;

    // Find the currently active lyric
    const activeLyric = lyrics.find(
        (l) => currentTime >= l.start && currentTime <= l.end
    );

    return (
        <AbsoluteFill
            style={{
                background: "linear-gradient(135deg, #0f0c29 0%, #1a1a2e 40%, #16213e 100%)",
                justifyContent: "center",
                alignItems: "center",
            }}
        >
            {/* Audio */}
            <Audio src={audioSrc} />

            {/* Subtle animated glow in background */}
            <div
                style={{
                    position: "absolute",
                    width: 600,
                    height: 600,
                    borderRadius: "50%",
                    background: "radial-gradient(circle, rgba(99, 102, 241, 0.15) 0%, transparent 70%)",
                    filter: "blur(80px)",
                    top: "50%",
                    left: "50%",
                    transform: `translate(-50%, -50%) scale(${1 + Math.sin(frame * 0.02) * 0.1})`,
                }}
            />

            {/* Lyric Display */}
            {activeLyric && (
                <LyricDisplay
                    text={activeLyric.text}
                    startFrame={activeLyric.start * fps}
                    endFrame={activeLyric.end * fps}
                    currentFrame={frame}
                />
            )}

            {/* Song title at top (subtle) */}
            <div
                style={{
                    position: "absolute",
                    top: 40,
                    left: 0,
                    right: 0,
                    textAlign: "center",
                    fontFamily: "'Inter', 'Segoe UI', sans-serif",
                    fontSize: 18,
                    color: "rgba(255, 255, 255, 0.3)",
                    letterSpacing: 4,
                    textTransform: "uppercase",
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
                }}
            />
        </AbsoluteFill>
    );
};

// Individual lyric line component with fade animation
const LyricDisplay: React.FC<{
    text: string;
    startFrame: number;
    endFrame: number;
    currentFrame: number;
}> = ({ text, startFrame, endFrame, currentFrame }) => {
    const fadeInDuration = 8; // frames
    const fadeOutDuration = 8;

    const opacity = interpolate(
        currentFrame,
        [startFrame, startFrame + fadeInDuration, endFrame - fadeOutDuration, endFrame],
        [0, 1, 1, 0],
        { extrapolateLeft: "clamp", extrapolateRight: "clamp" }
    );

    const translateY = interpolate(
        currentFrame,
        [startFrame, startFrame + fadeInDuration],
        [20, 0],
        { extrapolateLeft: "clamp", extrapolateRight: "clamp" }
    );

    return (
        <div
            style={{
                opacity,
                transform: `translateY(${translateY}px)`,
                fontFamily: "'Inter', 'Segoe UI', sans-serif",
                fontSize: 56,
                fontWeight: 700,
                color: "#ffffff",
                textAlign: "center",
                maxWidth: "80%",
                lineHeight: 1.4,
                textShadow: "0 0 40px rgba(99, 102, 241, 0.5), 0 4px 20px rgba(0,0,0,0.5)",
                letterSpacing: 1,
            }}
        >
            {text}
        </div>
    );
};
