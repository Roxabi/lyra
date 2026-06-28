import { ArrowCounterClockwise } from "@phosphor-icons/react";
import { useQuery } from "@tanstack/react-query";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { fetchSessions, resumeSession } from "@/lib/api";

interface ReprendrePanelProps {
  agent: string | null;
  onResumed: (agent: string) => void;
}

const PLATFORM_LABEL: Record<string, string> = {
  telegram: "Telegram",
  discord: "Discord",
  web: "Web",
};

export function ReprendrePanel({ agent, onResumed }: ReprendrePanelProps) {
  const { data, isLoading, refetch } = useQuery({
    queryKey: ["sessions", agent],
    queryFn: () => fetchSessions(agent ?? ""),
    enabled: Boolean(agent),
  });

  if (!agent) {
    return (
      <p className="text-sm text-muted-foreground">Sélectionnez un chat pour voir les sessions.</p>
    );
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
    <div className="space-y-3">
      <div>
        <h2 className="font-[family-name:var(--font-head)] text-sm font-semibold">Reprendre</h2>
        <p className="text-xs text-muted-foreground">Sessions récentes pour {agent}</p>
      </div>

      {isLoading ? <p className="text-xs text-muted-foreground">Chargement…</p> : null}

      <ul className="space-y-2">
        {sessions.length === 0 && !isLoading ? (
          <li className="rounded-lg border border-dashed border-border px-3 py-4 text-center text-xs text-muted-foreground">
            Aucune session à reprendre.
          </li>
        ) : null}
        {sessions.map((s) => (
          <li key={s.session_id}>
            <Card className="border-border/80 shadow-none">
              <CardContent className="flex items-start justify-between gap-2 p-3">
                <div className="min-w-0 space-y-1">
                  <Badge variant="secondary" className="text-[10px]">
                    {PLATFORM_LABEL[s.platform] ?? s.platform}
                  </Badge>
                  <p className="truncate text-sm text-foreground">{s.first_user_msg ?? "(vide)"}</p>
                  <p className="text-xs text-muted-foreground">{s.turn_count} tours</p>
                </div>
                <Button
                  size="sm"
                  variant="outline"
                  className="shrink-0 gap-1"
                  disabled={!s.cli_session_id}
                  onClick={() => onResume(s.cli_session_id)}
                >
                  <ArrowCounterClockwise className="size-3.5" aria-hidden />
                  Resume
                </Button>
              </CardContent>
            </Card>
          </li>
        ))}
      </ul>
    </div>
  );
}
