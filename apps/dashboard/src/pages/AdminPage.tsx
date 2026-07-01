import { Skeleton } from "@astryxdesign/core/Skeleton";
import { PencilSimple, Plus } from "@phosphor-icons/react";
import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { UserFormDialog } from "@/components/admin/UserFormDialog";
import { PageIntro } from "@/components/layout/PageIntro";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import { ListToolbar, ListToolbarHeader, ListToolbarSearch } from "@/components/ui/list-toolbar";
import { PresenceBadge } from "@/components/ui/presence-badge";
import { type AdminUserAccess, fetchAdminAccess } from "@/lib/admin-api";
import { useDebouncedValue } from "@/lib/use-debounced-value";

function filterUsers(users: AdminUserAccess[], query: string): AdminUserAccess[] {
  const q = query.trim().toLowerCase();
  if (!q) return users;
  return users.filter((u) => {
    const haystack = [u.display_name ?? "", u.email ?? "", ...u.agents].join(" ").toLowerCase();
    return haystack.includes(q);
  });
}

function AdminSkeleton() {
  const { t } = useTranslation("common");
  return (
    <div className="overflow-x-auto rounded-xl border border-border bg-card shadow-sm">
      <div
        role="status"
        className="divide-y divide-border/30 px-4"
        aria-busy="true"
        aria-label={t("actions.loading")}
      >
        {[0, 1, 2].map((i) => (
          <div key={i} className="flex items-center gap-4 py-3">
            <Skeleton width={128} height={16} />
            <Skeleton width={160} height={16} />
            <Skeleton width={48} height={24} radius="rounded" />
            <Skeleton width={48} height={24} radius="rounded" />
            <Skeleton width={80} height={24} radius="rounded" />
            <Skeleton width={64} height={28} radius={2} className="ml-auto" />
          </div>
        ))}
      </div>
    </div>
  );
}

export function AdminPage() {
  const { t } = useTranslation("admin");
  const { t: tc } = useTranslation("common");
  const [search, setSearch] = useState("");
  const [formOpen, setFormOpen] = useState(false);
  const [editingUser, setEditingUser] = useState<AdminUserAccess | null>(null);
  const debouncedSearch = useDebouncedValue(search, 300);

  const { data, isLoading, isError } = useQuery({
    queryKey: ["admin-access"],
    queryFn: fetchAdminAccess,
  });

  const users = data?.users ?? [];
  const filtered = useMemo(() => filterUsers(users, debouncedSearch), [users, debouncedSearch]);
  const hasFilters = search.trim().length > 0;

  const openCreate = () => {
    setEditingUser(null);
    setFormOpen(true);
  };

  const openEdit = (user: AdminUserAccess) => {
    setEditingUser(user);
    setFormOpen(true);
  };

  return (
    <div className="space-y-6 pb-8">
      <PageIntro>{t("subtitle")}</PageIntro>

      <div className="flex flex-col gap-4 p-4">
        <UserFormDialog open={formOpen} onOpenChange={setFormOpen} user={editingUser} />

        <ListToolbar>
          <ListToolbarHeader
            meta={isLoading ? tc("actions.loading") : t("count", { count: filtered.length })}
            actions={
              <Button type="button" size="sm" className="h-8 gap-1.5" onClick={openCreate}>
                <Plus className="size-3.5" aria-hidden />
                {t("createUser")}
              </Button>
            }
          />
          <ListToolbarSearch
            value={search}
            onChange={setSearch}
            placeholder={t("searchPlaceholder")}
            aria-label={tc("search")}
          />
        </ListToolbar>

        {isLoading ? <AdminSkeleton /> : null}

        {isError ? (
          <p className="text-sm text-destructive" role="alert">
            {t("loadError")}
          </p>
        ) : null}

        {!isLoading && !isError && filtered.length === 0 ? (
          <EmptyState
            title={hasFilters ? t("emptyFiltered") : t("empty")}
            hint={hasFilters ? undefined : t("emptyHint")}
            action={
              hasFilters ? undefined : (
                <Button type="button" size="sm" className="gap-1.5" onClick={openCreate}>
                  <Plus className="size-3.5" aria-hidden />
                  {t("createUser")}
                </Button>
              )
            }
          />
        ) : null}

        {!isLoading && !isError && filtered.length > 0 ? (
          <div className="overflow-x-auto rounded-xl border border-border bg-card shadow-sm">
            <table className="w-full min-w-[720px] text-left text-sm">
              <thead>
                <tr className="border-b border-border/50 text-xs text-muted-foreground">
                  <th className="px-4 py-2 pr-4 font-medium">{t("colDisplayName")}</th>
                  <th className="py-2 pr-4 font-medium">{t("colEmail")}</th>
                  <th className="py-2 pr-4 font-medium">{t("colTelegram")}</th>
                  <th className="py-2 pr-4 font-medium">{t("colDiscord")}</th>
                  <th className="py-2 pr-4 font-medium">{t("colAgents")}</th>
                  <th className="py-2 pr-4 pl-2 text-right font-medium">{t("colAction")}</th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((u) => (
                  <tr
                    key={u.user_id}
                    className="border-b border-border/30 last:border-0 hover:bg-muted/20"
                  >
                    <td className="px-4 py-3 pr-4 text-sm font-medium">{u.display_name ?? "—"}</td>
                    <td className="py-3 pr-4 text-sm text-muted-foreground">{u.email ?? "—"}</td>
                    <td className="py-3 pr-4">
                      <PresenceBadge present={u.telegram != null} />
                    </td>
                    <td className="py-3 pr-4">
                      <PresenceBadge present={u.discord != null} />
                    </td>
                    <td className="py-3 pr-4">
                      <div className="flex flex-wrap gap-1.5">
                        {u.agents.length > 0 ? (
                          u.agents.map((agent) => (
                            <Badge
                              key={agent}
                              variant="secondary"
                              className="font-mono text-[10px]"
                            >
                              {agent}
                            </Badge>
                          ))
                        ) : (
                          <span className="text-xs text-muted-foreground">{t("noAgents")}</span>
                        )}
                      </div>
                    </td>
                    <td className="py-3 pr-4 pl-2 text-right">
                      <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        className="h-7 gap-1.5 px-2.5 text-xs"
                        onClick={() => openEdit(u)}
                      >
                        <PencilSimple className="size-3.5" aria-hidden />
                        {t("editUser")}
                      </Button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
      </div>
    </div>
  );
}
