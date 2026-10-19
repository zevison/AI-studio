/** Exact-request, short-lived host capability; never carries an OpenLux API key. */
import type { BackendPort } from './official-provider.js';
export declare function loopbackBackend(address: string, token: string): BackendPort;
