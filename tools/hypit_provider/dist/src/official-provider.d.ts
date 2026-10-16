import type { EndpointRequest } from '@hypit/hypit/endpoint';
export declare const providerName = "@seedance-studio/openlux-provider";
export type PollResult = {
    state: 'pending' | 'ready' | 'failed';
};
/** Trusted backend port: approval must bind project, price, account and request before submit. */
export interface BackendPort {
    authorize(payload: Record<string, unknown>): Promise<void>;
    submit(payload: Record<string, unknown>, identity: string): Promise<string>;
    poll(id: string): Promise<PollResult>;
    collect(id: string): Promise<Uint8Array>;
}
type Row = {
    fingerprint: string;
    state: string;
    remote: string | null;
    created: number;
};
export declare class OperationLedger {
    private path;
    constructor(path: string);
    private withDb;
    read(operation: string): Row | undefined;
    reserve(operation: string, fingerprint: string, now: number): void;
    update(operation: string, state: string, remote: string | null): void;
    close(): void;
}
/** Only verified scalar mapping is currently supported; unsupported media is refused before I/O. */
export declare function officialPayload(request: EndpointRequest): Record<string, unknown>;
export declare function createOfficialProvider(options: {
    instance: string;
    pool: string;
    account: string;
    ledger: OperationLedger;
    backend?: BackendPort;
    clock?: () => number;
    timeoutMs?: number;
    actionTimeoutMs?: number;
}): import("@hypit/hypit/endpoint").EndpointInstance;
export {};
