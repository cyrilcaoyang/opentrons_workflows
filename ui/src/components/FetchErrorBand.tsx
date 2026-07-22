import type { FetchError } from "../lib/types";
import { MessageBand } from "./MessageBand";

/**
 * Transport-level reachability failure: the browser's poll of the gateway's
 * `/status` failed. Reader-side ("unreachable"), not a device-reported fault —
 * but still "something is wrong," so it wears the rose tone.
 */
export function FetchErrorBand({ error }: { error: FetchError }) {
  return (
    <MessageBand tone="rose">
      <span className="block font-medium">Browser could not reach the gateway</span>
      <span className="block font-mono">
        {error.kind}
        {error.http_status ? ` · HTTP ${error.http_status}` : ""}
      </span>
    </MessageBand>
  );
}
