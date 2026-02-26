import React from "react";
import { Composition, staticFile, getInputProps } from "remotion";
import { getAudioDurationInSeconds } from "@remotion/media-utils";
import { LyricVideo } from "./LyricVideo";
import type { LyricVideoProps } from "./LyricVideo";
import lyricsData from "../public/lyrics.json";

const FPS = 30;

export const RemotionRoot: React.FC = () => {
    return (
        <>
            <Composition
                id="LyricVideo"
                component={LyricVideo as unknown as React.FC<Record<string, unknown>>}
                fps={FPS}
                width={1920}
                height={1080}
                defaultProps={{
                    audioSrc: staticFile("audio.mp3"),
                    lyrics: lyricsData,
                } as Record<string, unknown>}
                calculateMetadata={async () => {
                    const audioDuration = await getAudioDurationInSeconds(
                        staticFile("audio.mp3")
                    );
                    return {
                        durationInFrames: Math.ceil(audioDuration * FPS),
                    };
                }}
            />
        </>
    );
};
