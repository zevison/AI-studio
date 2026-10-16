export declare class ContractViolation extends Error {
    constructor(message: string);
}
export declare class SubmissionAmbiguous extends Error {
    constructor();
}
export type ReferenceRole = 'reference_image' | 'reference_video' | 'reference_audio' | 'first_frame' | 'last_frame';
export type Reference = {
    role: ReferenceRole;
    url: string;
    personReference?: boolean;
};
export type VideoSpec = {
    prompt: string;
    taskType: 'text' | 'reference' | 'edit' | 'extend';
    references: Reference[];
    duration: number;
    ratio: string;
    resolution: '480p' | '720p' | '1080p';
    generateAudio: boolean;
};
export declare function externalHttps(value: unknown): string;
/**
 * Preserve all reference roles; a required personReference value is not silently dropped.
 * Visual person classification has no VERIFIED OpenLux wire mapping yet, so reject any
 * affected operation before it can become a paid request.
 */
export declare function buildOpenLuxVideoPayload(spec: VideoSpec): Record<string, unknown>;
export declare function requestFingerprint(account: string, payload: Record<string, unknown>): string;
export declare function remoteTaskId(response: unknown): string;
export declare function remoteState(response: unknown): 'pending' | 'ready' | 'failed';
export declare function finalVideoUrl(response: unknown): string;
export declare function assertPollIdentity(response: unknown, expectedId: string): void;
