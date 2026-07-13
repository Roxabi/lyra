import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { createOrg, fetchOrgs } from "@/features/auth/api";
import { useAuth } from "@/features/auth/auth-context";

export function OrgSwitcher() {
  const { t } = useTranslation("auth");
  const { status, activeOrgId, setOrg } = useAuth();
  const qc = useQueryClient();
  const [name, setName] = useState("");
  const orgsQ = useQuery({
    queryKey: ["auth", "orgs"],
    queryFn: fetchOrgs,
    enabled: status === "authenticated",
  });

  const create = useMutation({
    mutationFn: () => createOrg(name.trim()),
    onSuccess: async (data) => {
      setName("");
      setOrg(data.org.id);
      await qc.invalidateQueries({ queryKey: ["auth", "orgs"] });
    },
  });

  if (status !== "authenticated") return null;

  const orgs = orgsQ.data?.orgs ?? [];
  const current = orgs.find((o) => o.id === activeOrgId);

  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        className="inline-flex h-8 max-w-[10rem] items-center justify-center truncate rounded-lg border border-border bg-background px-2.5 text-sm font-medium hover:bg-muted"
        type="button"
      >
        {current?.name ?? t("org.personal")}
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-56">
        <DropdownMenuLabel>{t("org.switch")}</DropdownMenuLabel>
        <DropdownMenuItem onClick={() => setOrg(null)}>{t("org.personal")}</DropdownMenuItem>
        <DropdownMenuSeparator />
        {orgs.map((o) => (
          <DropdownMenuItem key={o.id} onClick={() => setOrg(o.id)}>
            {o.name}
          </DropdownMenuItem>
        ))}
        <DropdownMenuSeparator />
        {/*
          Native controls (not Button-as-Item) to avoid Base UI #31:
          nested useButton(native) mismatches inside Menu.
        */}
        <div className="flex flex-col gap-2 p-2">
          <Input
            placeholder={t("org.newName")}
            value={name}
            onChange={(e) => setName(e.target.value)}
            onKeyDown={(e) => e.stopPropagation()}
            onClick={(e) => e.stopPropagation()}
          />
          <button
            type="button"
            className="inline-flex h-7 items-center justify-center rounded-md bg-primary px-2 text-xs font-medium text-primary-foreground disabled:opacity-50"
            disabled={!name.trim() || create.isPending}
            onClick={(e) => {
              e.preventDefault();
              e.stopPropagation();
              create.mutate();
            }}
          >
            {t("org.create")}
          </button>
        </div>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
