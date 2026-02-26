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

// ─── Improved LyricDisplay ──────────────────────────────────────────────────
// Features:
//  1. Robust word-to-line mapping (newline-position based)
//  2. Stable React keys (absolute line index)
//  3. Smooth vertical slide when 2-line window shifts
//  4. Dimmed preview of the next upcoming line
//  5. Dynamic font sizing for long lines

const MAX_VISIBLE_LINES = 2;
const SLIDE_TRANSITION_FRAMES = 10; // frames for smooth slide between line windows
const BASE_FONT_SIZE = 56;
const MIN_FONT_SIZE = 36;
const LONG_LINE_CHAR_THRESHOLD = 30; // start shrinking after this many chars

/**
 * Robustly map words to lines using newline positions in text.
 * Instead of counting words per line (fragile), we find which words
 * belong to each line based on cumulative word consumption.
 */
const mapWordsToLines = (
    text: string,
    words: WordTimestamp[]
): { lineText: string; words: WordTimestamp[]; absoluteIndex: number }[] => {
    const lines = text.split("\n");
    const result: { lineText: string; words: WordTimestamp[]; absoluteIndex: number }[] = [];
    let wordIdx = 0;

    for (let i = 0; i < lines.length; i++) {
        const lineText = lines[i].trim();
        if (!lineText) {
            result.push({ lineText, words: [], absoluteIndex: i });
            continue;
        }
        const lineWordCount = lineText.split(/\s+/).filter(Boolean).length;
        const lineWords = words.slice(wordIdx, wordIdx + lineWordCount);
        wordIdx += lineWordCount;
        result.push({ lineText, words: lineWords, absoluteIndex: i });
    }

    // If we still have leftover words, attach them to the last line
    if (wordIdx < words.length && result.length > 0) {
        result[result.length - 1].words.push(...words.slice(wordIdx));
    }

    return result;
};

/**
 * Compute dynamic font size based on the longest visible line.
 */
const computeFontSize = (
    visibleLines: { lineText: string }[]
): number => {
    const maxChars = Math.max(...visibleLines.map((l) => l.lineText.length), 1);
    if (maxChars <= LONG_LINE_CHAR_THRESHOLD) return BASE_FONT_SIZE;
    // Linearly shrink between BASE and MIN over the range [THRESHOLD .. THRESHOLD*2]
    const ratio = Math.min(
        1,
        (maxChars - LONG_LINE_CHAR_THRESHOLD) / LONG_LINE_CHAR_THRESHOLD
    );
    return Math.round(BASE_FONT_SIZE - (BASE_FONT_SIZE - MIN_FONT_SIZE) * ratio);
};

const LyricDisplay: React.FC<{
    text: string;
    words?: WordTimestamp[];
    startFrame: number;
    endFrame: number;
    currentFrame: number;
    currentTime: number;
    fps: number;
}> = ({ text, words, startFrame, endFrame, currentFrame, currentTime, fps }) => {
    const totalFrames = endFrame - startFrame;
    const safeFade = Math.min(8, Math.floor(totalFrames / 3));

    // Overall fade in/out for the entire lyric block
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

    // ── Build line groups ──
    const allLines = words && words.length > 0
        ? mapWordsToLines(text, words)
        : text.split("\n").map((lineText, i) => ({
            lineText: lineText.trim(),
            words: [] as WordTimestamp[],
            absoluteIndex: i,
        }));

    // ── Find active line index (which line has the currently sung word) ──
    let activeLineIdx = 0;
    for (let i = 0; i < allLines.length; i++) {
        for (const w of allLines[i].words) {
            if (currentTime >= w.start) {
                activeLineIdx = i;
            }
        }
    }

    // ── Compute visible window (max 2 lines) ──
    let windowStart = 0;
    if (allLines.length > MAX_VISIBLE_LINES) {
        windowStart = Math.min(activeLineIdx, allLines.length - MAX_VISIBLE_LINES);
    }
    const visibleLines = allLines.slice(windowStart, windowStart + MAX_VISIBLE_LINES);

    // ── Preview line: the line right after the visible window (dimmed) ──
    const previewLine =
        windowStart + MAX_VISIBLE_LINES < allLines.length
            ? allLines[windowStart + MAX_VISIBLE_LINES]
            : null;

    // ── Smooth slide transition ──
    // When the window shifts, animate a slight upward slide
    // We detect the shift by looking at the first visible word's start time
    let slideOffset = 0;
    if (allLines.length > MAX_VISIBLE_LINES && visibleLines[0].words.length > 0) {
        // The moment the window changes is when the first word of the current
        // visible first line starts. Animate around that point.
        const windowChangeTime = visibleLines[0].words[0].start;
        const windowChangeFrame = windowChangeTime * fps;
        if (
            currentFrame >= windowChangeFrame &&
            currentFrame <= windowChangeFrame + SLIDE_TRANSITION_FRAMES
        ) {
            slideOffset = interpolate(
                currentFrame,
                [windowChangeFrame, windowChangeFrame + SLIDE_TRANSITION_FRAMES],
                [-30, 0],
                { extrapolateLeft: "clamp", extrapolateRight: "clamp" }
            );
        }
    }

    // ── Dynamic font size ──
    const fontSize = computeFontSize(visibleLines);

    // ── Render a single word with karaoke highlighting ──
    const renderWord = (w: WordTimestamp, keyPrefix: string, idx: number) => {
        const isActive = currentTime >= w.start && currentTime <= w.end;
        const isPast = currentTime > w.end;

        const wordStartFrame = w.start * fps;
        const wordEndFrame = w.end * fps;
        const frameDuration = wordEndFrame - wordStartFrame;

        let highlightProgress: number;
        if (frameDuration <= 3) {
            highlightProgress = isActive || isPast ? 1 : 0;
        } else {
            highlightProgress = interpolate(
                currentFrame,
                [wordStartFrame, wordStartFrame + 2, wordEndFrame - 1, wordEndFrame],
                [0, 1, 1, 0],
                { extrapolateLeft: "clamp", extrapolateRight: "clamp" }
            );
        }

        let color = "#ffffff";
        let textShadow =
            "0 0 40px rgba(99, 102, 241, 0.5), 0 4px 20px rgba(0,0,0,0.5)";

        if (isActive) {
            color = `rgb(255, ${255 - Math.round(196 * highlightProgress)}, ${255 - Math.round(196 * highlightProgress)})`;
            textShadow = `0 0 30px rgba(255, 59, 59, ${0.6 * highlightProgress}), 0 0 60px rgba(255, 59, 59, ${0.3 * highlightProgress}), 0 4px 20px rgba(0,0,0,0.5)`;
        } else if (isPast) {
            color = "rgba(255, 120, 120, 0.7)";
            textShadow =
                "0 0 20px rgba(255, 59, 59, 0.2), 0 4px 20px rgba(0,0,0,0.5)";
        }

        const scale = isActive ? 1 + 0.05 * highlightProgress : 1;

        return (
            <span
                key={`${keyPrefix}-${idx}`}
                style={{
                    color,
                    textShadow,
                    display: "inline-block",
                    transform: `scale(${scale})`,
                    marginRight: "0.25em",
                }}
            >
                {w.word}
            </span>
        );
    };

    return (
        <div
            style={{
                opacity: blockOpacity,
                transform: `translateY(${blockTranslateY + slideOffset}px)`,
                fontFamily:
                    "'Noto Sans Devanagari', 'Mangal', 'Kokila', sans-serif",
                fontSize,
                fontWeight: 700,
                color: "#ffffff",
                textAlign: "center",
                maxWidth: "80%",
                lineHeight: 1.5,
                whiteSpace: "pre-wrap",
                textShadow:
                    words && words.length > 0
                        ? "none"
                        : "0 0 40px rgba(99, 102, 241, 0.5), 0 4px 20px rgba(0,0,0,0.5)",
                letterSpacing: 1,
            }}
        >
            {/* ── Active visible lines ── */}
            {visibleLines.map((line, idx) => (
                <div
                    key={`line-abs-${line.absoluteIndex}`}
                    style={{ marginBottom: idx < visibleLines.length - 1 ? 8 : 0 }}
                >
                    {line.words.length > 0
                        ? line.words.map((w, i) =>
                            renderWord(w, `L${line.absoluteIndex}`, i)
                        )
                        : line.lineText}
                </div>
            ))}

            {/* ── Dimmed preview of the next line ── */}
            {previewLine && previewLine.lineText && (
                <div
                    key={`line-abs-${previewLine.absoluteIndex}`}
                    style={{
                        marginTop: 12,
                        opacity: 0.25,
                        fontSize: Math.round(fontSize * 0.75),
                        color: "rgba(255, 255, 255, 0.5)",
                        textShadow: "none",
                    }}
                >
                    {previewLine.lineText}
                </div>
            )}
        </div>
    );
};
