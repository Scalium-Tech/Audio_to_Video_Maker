import React from "react";
import { Composition, staticFile } from "remotion";
import { LyricVideo } from "./LyricVideo";
import type { LyricVideoProps } from "./LyricVideo";
import lyricsData from "../public/lyrics.json";

const FPS = 30;

// Calculate total duration from lyrics
const lastLyric = lyricsData[lyricsData.length - 1];
const totalDurationSec = Math.ceil(lastLyric.end) + 3; // Add 3 sec buffer at end
const totalFrames = totalDurationSec * FPS;

export const RemotionRoot: React.FC = () => {
    return (
        <>
            <Composition
                id="LyricVideo"
                component={LyricVideo as unknown as React.FC<Record<string, unknown>>}
                durationInFrames={totalFrames}
                fps={FPS}
                width={1920}
                height={1080}
                defaultProps={{
                    audioSrc: staticFile("audio.mp3"),
                    lyrics: lyricsData,
                } as Record<string, unknown>}
            />
        </>
    );
};
