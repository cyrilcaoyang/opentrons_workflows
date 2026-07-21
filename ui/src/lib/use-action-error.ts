import { useCallback, useState } from "react";

import { interpretActionError, type ActionError, type Parse412 } from "./action-error";

/**
 * Owns the inline action-error state for the control panel: clicking a
 * control clears any prior error; on failure the device's refusal is
 * interpreted (412/423/409 shapes) and surfaced in the ActionErrorBadge.
 */
export function useActionErrorState(parse412?: Parse412) {
  const [actionError, setActionError] = useState<ActionError | null>(null);

  const clearError = useCallback(() => setActionError(null), []);

  const reportError = useCallback(
    (err: unknown, action?: string) =>
      setActionError(interpretActionError(err, { action, parse412 })),
    [parse412],
  );

  return { actionError, setActionError, clearError, reportError };
}
