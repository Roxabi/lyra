import { useQuery } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { fetchSessions, resumeSession } from "@/lib/api";

interface ReprendrePanelProps {
  agent: string | null;
  onResumed: (agent: string) => void;
}

const PLATFORM_LABEL: Record<string, string> = {
  telegram: "TG",
  discord: "DC",
  web: "Web",
};

export function ReprendrePanel({ agent, onResumed }: ReprendrePanelProps) {
  const { data, isLoading, refetch } = useQuery({
    queryKey: ["sessions", agent],
    queryFn: () => fetchSessions(agent ?? ""),
    enabled: Boolean(agent),
  });

  if (!agent) {
    return <p className="text-xs text-muted-foreground">Select a chat to list sessions.</p>;
  }

  const sessions = data ?? [];

  const onResume = async (cliId: string | null) => {
    if (!cliId) return;
    const res = await resumeSession(agent, cliId);
    if (res.accepted) {
      onResumed(agent);
      void refetch();
    }
  };

  return (
    <div className="space-y-2">
      <p className="eyebrow">Reprendre</p>
      {isLoading ? <p className="text-xs text-muted-foreground">Loading…</p> : null}
      <ul className="max-h-48 space-y-1 overflow-auto text-xs">
        {sessions.map((s) => (
          <li
            key={s.session_id}
            className="flex items-start justify-between gap-2 rounded border border-border p-2"
          >
            <div className="min-w-0">
              <span className="mono mr-1 rounded bg-muted px-1 text-[10px]">
                {PLATFORM_LABEL[s.platform] ?? s.platform}
              </span>
              <span className="block truncate">{s.first_user_msg ?? "(empty)"}</span>
              <span className="text-muted-foreground">{s.turn_count} turns</span>
            </div>
            <Button
              size="sm"
              variant="outline"
              disabled={!s.cli_session_id}
              onClick={() => onResume(s.cli_session_id)}
            >
              Resume
            </Button>
          </li>
        ))}
      </ul>
    </div>
  );
}
