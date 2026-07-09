import { useQuery } from "@tanstack/react-query";
import { Pencil, Plus } from "lucide-react";
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { TableCell, TableHead, TableRow } from "@/components/ui/table";
import { type AdminUserAccess, fetchAdminAccess } from "@/features/users/api";
import { UserFormDialog } from "@/features/users/components/user-form-dialog";
import { DataTable, DataTableBody, DataTableHeader } from "@/shared/components/data-table";
import { EmptyState } from "@/shared/components/empty-state";
import {
  ListToolbar,
  ListToolbarHeader,
  ListToolbarSearch,
} from "@/shared/components/list-toolbar";
import { PageIntro } from "@/shared/components/page-intro";
import { PresenceBadge } from "@/shared/components/presence-badge";
import { useDebouncedValue } from "@/shared/hooks/use-debounced-value";

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
    <div className="overflow-x-auto rounded-xl border bg-card shadow-sm">
      <div
        role="status"
        className="divide-y px-4"
        aria-busy="true"
        aria-label={t("actions.loading")}
      >
        {[0, 1, 2].map((i) => (
          <div key={i} className="flex items-center gap-4 py-3">
            <Skeleton className="h-4 w-32" />
            <Skeleton className="h-4 w-40" />
            <Skeleton className="h-6 w-10 rounded-full" />
            <Skeleton className="h-6 w-10 rounded-full" />
            <Skeleton className="h-6 w-20 rounded-full" />
            <Skeleton className="ml-auto h-7 w-16" />
          </div>
        ))}
      </div>
    </div>
  );
}

export function UsersPage() {
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
            description={hasFilters ? undefined : t("emptyHint")}
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
          <DataTable>
            <DataTableHeader>
              <TableHead>{t("colDisplayName")}</TableHead>
              <TableHead>{t("colEmail")}</TableHead>
              <TableHead>{t("colTelegram")}</TableHead>
              <TableHead>{t("colDiscord")}</TableHead>
              <TableHead>{t("colAgents")}</TableHead>
              <TableHead className="text-right">{t("colAction")}</TableHead>
            </DataTableHeader>
            <DataTableBody>
              {filtered.map((u) => (
                <TableRow key={u.user_id}>
                  <TableCell className="font-medium">{u.display_name ?? "—"}</TableCell>
                  <TableCell className="text-muted-foreground">{u.email ?? "—"}</TableCell>
                  <TableCell>
                    <PresenceBadge platform="telegram" present={u.telegram != null} />
                  </TableCell>
                  <TableCell>
                    <PresenceBadge platform="discord" present={u.discord != null} />
                  </TableCell>
                  <TableCell>
                    <div className="flex flex-wrap gap-1.5">
                      {u.agents.length > 0 ? (
                        u.agents.map((agent) => (
                          <Badge key={agent} variant="outline" className="font-mono text-[10px]">
                            {agent}
                          </Badge>
                        ))
                      ) : (
                        <span className="text-xs text-muted-foreground">{t("noAgents")}</span>
                      )}
                    </div>
                  </TableCell>
                  <TableCell className="text-right">
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      className="h-7 gap-1.5 px-2.5 text-xs"
                      onClick={() => openEdit(u)}
                    >
                      <Pencil className="size-3.5" aria-hidden />
                      {t("editUser")}
                    </Button>
                  </TableCell>
                </TableRow>
              ))}
            </DataTableBody>
          </DataTable>
        ) : null}
      </div>
    </div>
  );
}
