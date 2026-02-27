import React from "react";
import { Composition, staticFile, getInputProps } from "remotion";
import { getAudioDurationInSeconds } from "@remotion/media-utils";
import { LyricVideo } from "./LyricVideo";
import type { LyricVideoProps } from "./LyricVideo";
import lyricsData from "../public/lyrics.json";

const FPS = 30;

// getInputProps() receives --props from CLI, allowing parallel workers
// to render with different files without conflicts
const inputProps = getInputProps();

export const RemotionRoot: React.FC = () => {
    // Use input props if provided (parallel mode), otherwise use defaults (studio mode)
    const audioFile = (inputProps?.audioFile as string) || "audio.mp3";
    const bgFile = (inputProps?.bgFile as string) || "background.jpg";
    const lyrics = (inputProps?.lyrics as typeof lyricsData) || lyricsData;

    return (
        <>
            <Composition
                id="LyricVideo"
                component={LyricVideo as unknown as React.FC<Record<string, unknown>>}
                fps={FPS}
                width={1920}
                height={1080}
                defaultProps={{
                    audioSrc: staticFile(audioFile),
                    lyrics: lyrics,
                    backgroundImage: staticFile(bgFile),
                } as Record<string, unknown>}
                calculateMetadata={async () => {
                    const audioDuration = await getAudioDurationInSeconds(
                        staticFile(audioFile)
                    );
                    return {
                        durationInFrames: Math.ceil(audioDuration * FPS),
                    };
                }}
            />
        </>
    );
};
