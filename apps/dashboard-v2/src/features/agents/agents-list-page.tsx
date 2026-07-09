import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { fetchAgentsConfigList } from "@/features/agents/api";
import { AgentsListPanel } from "@/features/agents/components/agents-list-panel";
import { PageIntro } from "@/shared/components/page-intro";

export function AgentsListPage() {
  const { t } = useTranslation("agents");
  const { data, isLoading, isError } = useQuery({
    queryKey: ["agents-config"],
    queryFn: fetchAgentsConfigList,
  });

  return (
    <div className="space-y-6 pb-8">
      <PageIntro>{t("subtitle")}</PageIntro>
      <AgentsListPanel agents={data?.agents ?? []} isLoading={isLoading} isError={isError} />
    </div>
  );
}
