import { useQuery } from "@tanstack/react-query";
import { fetchAgentsConfigList } from "@/features/agents/api";
import { AgentsListPanel } from "@/features/agents/components/agents-list-panel";
import { PageIntro } from "@/shared/components/page-intro";

export function AgentsListPage() {
  const { data, isLoading, isError } = useQuery({
    queryKey: ["agents-config"],
    queryFn: fetchAgentsConfigList,
  });

  return (
    <div className="space-y-6 pb-8">
      <PageIntro>Configure agent identity, harness defaults, and soul documents.</PageIntro>
      <AgentsListPanel agents={data?.agents ?? []} isLoading={isLoading} isError={isError} />
    </div>
  );
}
