import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { displayAgentName } from "@/lib/agents";
import { fetchAgentStatus, fetchAgents } from "@/lib/api";
import { loadTabs } from "@/lib/chats-storage";

export function DashboardHome() {
  const tabs = loadTabs();
  const { data: agents = [] } = useQuery({ queryKey: ["agents"], queryFn: fetchAgents });
  const { data: status = [] } = useQuery({
    queryKey: ["agent-status-all"],
    queryFn: () => fetchAgentStatus(),
    refetchInterval: 30_000,
  });

  const online = status.filter((s) => s.online).length;
  const offline = status.length - online;

  return (
    <div className="fd-scroll flex-1 overflow-y-auto p-6">
      <div className="mx-auto max-w-5xl space-y-6">
        <div>
          <h1 className="font-[family-name:var(--font-head)] text-2xl font-bold">Dashboard</h1>
          <p className="text-sm text-muted-foreground">
            Vue d'ensemble — chats actifs, workers et supervision.
          </p>
        </div>

        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <Card className="border-0 bg-muted/30 shadow-none">
            <CardHeader className="pb-2">
              <CardTitle className="text-sm font-medium text-muted-foreground">
                Chats actifs
              </CardTitle>
            </CardHeader>
            <CardContent>
              <p className="text-3xl font-semibold">{tabs.length}</p>
              <Link to="/chat" className="text-xs text-brand hover:underline">
                Ouvrir le chat →
              </Link>
            </CardContent>
          </Card>
          <Card className="border-0 bg-muted/30 shadow-none">
            <CardHeader className="pb-2">
              <CardTitle className="text-sm font-medium text-muted-foreground">Agents</CardTitle>
            </CardHeader>
            <CardContent>
              <p className="text-3xl font-semibold">{agents.length}</p>
              <p className="text-xs text-muted-foreground">{online} en ligne</p>
            </CardContent>
          </Card>
          <Card className="border-0 bg-muted/30 shadow-none">
            <CardHeader className="pb-2">
              <CardTitle className="text-sm font-medium text-muted-foreground">Jobs</CardTitle>
            </CardHeader>
            <CardContent>
              <p className="text-3xl font-semibold">—</p>
              <Link to="/jobs" className="text-xs text-brand hover:underline">
                Voir les jobs →
              </Link>
            </CardContent>
          </Card>
          <Card className="border-0 bg-muted/30 shadow-none">
            <CardHeader className="pb-2">
              <CardTitle className="text-sm font-medium text-muted-foreground">Ops</CardTitle>
            </CardHeader>
            <CardContent>
              <p className="text-3xl font-semibold">{offline}</p>
              <p className="text-xs text-muted-foreground">agents hors ligne</p>
            </CardContent>
          </Card>
        </div>

        <Card className="border-0 bg-muted/20 shadow-none">
          <CardHeader>
            <CardTitle className="text-base">État des agents</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            {status.length === 0 ? (
              <p className="text-sm text-muted-foreground">Aucun agent dans le roster.</p>
            ) : (
              status.map((s) => (
                <div
                  key={s.agent}
                  className="flex items-center justify-between rounded-lg bg-background/40 px-3 py-2"
                >
                  <span className="text-sm font-medium">{displayAgentName(s.agent)}</span>
                  <div className="flex items-center gap-2">
                    <span className="text-xs text-muted-foreground">{s.harness}</span>
                    <Badge variant={s.online ? "success" : "destructive"}>
                      {s.online ? "En ligne" : "Hors ligne"}
                    </Badge>
                  </div>
                </div>
              ))
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
