import { useEffect } from "react";

import { AssistantBubble } from "./components/AssistantBubble";
import { ControlPanel } from "./components/ControlPanel";
import { PlanReviewPanel } from "./components/PlanReviewPanel";
import { panelTitle } from "./lib/format";
import { useClaim } from "./lib/use-claim";
import { useGatewayStatus } from "./lib/use-status";

export function App() {
  const { snapshot, isPending, refetch } = useGatewayStatus(3000);
  const claim = useClaim("ot2-gateway-ui");

  // Name the tab after this robot, so two open panels are told apart.
  const name = snapshot?.name;
  useEffect(() => {
    if (name) document.title = panelTitle(name);
  }, [name]);

  return (
    <main className="mx-auto flex min-h-screen w-full max-w-5xl flex-col gap-4 p-4 sm:p-6">
      {isPending && !snapshot && (
        <p className="text-sm text-ink-muted dark:text-slate-400">Loading gateway status…</p>
      )}
      {!isPending && !snapshot && (
        <p className="rounded-md border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-900 dark:border-rose-900/50 dark:bg-rose-900/20 dark:text-rose-200">
          Could not reach the gateway&apos;s <span className="font-mono">/status</span>. Is the
          service running on this host?
        </p>
      )}
      {snapshot && <ControlPanel snapshot={snapshot} refetch={refetch} claim={claim} />}
      {/* Below the controls, not above: the panel appears and disappears with
          the plans themselves, and on top it shoved the whole page down every
          time an agent drafted something. The chat's plan card is the primary
          review surface now; this is the overview (other agents' proposals,
          settled plans), reachable via each card's "view in panel" link. */}
      {snapshot && <PlanReviewPanel claim={claim} />}
      <AssistantBubble claim={claim} />
    </main>
  );
}
