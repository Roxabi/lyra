import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Spinner } from "@/components/ui/spinner";
import { fetchAgentsConfigList } from "@/features/agents/api";
import { type AdminUserAccess, createAdminUser, patchAdminUser } from "@/features/users/api";
import { FilterChip } from "@/shared/components/filter-chip";
import { bffErrorMessage } from "@/shared/lib/bff-errors";

const EMAIL_RE = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;

interface UserFormDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  user?: AdminUserAccess | null;
}

export function UserFormDialog({ open, onOpenChange, user }: UserFormDialogProps) {
  const { t } = useTranslation("admin");
  const qc = useQueryClient();
  const isEdit = Boolean(user);

  const [displayName, setDisplayName] = useState("");
  const [email, setEmail] = useState("");
  const [telegramUid, setTelegramUid] = useState("");
  const [discordUid, setDiscordUid] = useState("");
  const [selectedAgents, setSelectedAgents] = useState<string[]>([]);

  const {
    data: agentsData,
    isLoading: agentsLoading,
    isError: agentsError,
  } = useQuery({
    queryKey: ["agents-config"],
    queryFn: fetchAgentsConfigList,
    enabled: open,
  });

  const availableAgents = agentsData?.agents.map((a) => a.name) ?? [];

  useEffect(() => {
    if (!open) return;
    setDisplayName(user?.display_name ?? "");
    setEmail(user?.email ?? "");
    setTelegramUid(user?.telegram?.platform_uid ?? "");
    setDiscordUid(user?.discord?.platform_uid ?? "");
    setSelectedAgents(user?.agents ?? []);
  }, [open, user]);

  const nameValid = displayName.trim().length > 0;
  const emailValid = EMAIL_RE.test(email.trim());

  const reset = () => {
    setDisplayName("");
    setEmail("");
    setTelegramUid("");
    setDiscordUid("");
    setSelectedAgents([]);
  };

  const toggleAgent = (agent: string) => {
    setSelectedAgents((prev) =>
      prev.includes(agent) ? prev.filter((a) => a !== agent) : [...prev, agent],
    );
  };

  const saveMut = useMutation({
    mutationFn: async () => {
      const body = {
        display_name: displayName.trim(),
        email: email.trim(),
        telegram_uid: telegramUid.trim() || null,
        discord_uid: discordUid.trim() || null,
        agents: selectedAgents,
      };
      if (isEdit && user) {
        return patchAdminUser(user.user_id, body);
      }
      return createAdminUser(body);
    },
    onSuccess: () => {
      toast.info(isEdit ? t("editSuccess") : t("createSuccess"));
      void qc.invalidateQueries({ queryKey: ["admin-access"] });
      onOpenChange(false);
      reset();
    },
  });

  const handleOpenChange = (next: boolean) => {
    if (!next) reset();
    onOpenChange(next);
  };

  const title = isEdit ? t("editTitle") : t("createTitle");

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>
            {isEdit ? t("editDescription") : t("createDescription")}
          </DialogDescription>
        </DialogHeader>

        <form
          className="space-y-4"
          onSubmit={(e) => {
            e.preventDefault();
            if (!nameValid || !emailValid || saveMut.isPending) return;
            saveMut.mutate();
          }}
        >
          {saveMut.isError ? (
            <Alert variant="destructive">
              <AlertDescription>
                {bffErrorMessage(saveMut.error, t, isEdit ? "edit" : "create")}
              </AlertDescription>
            </Alert>
          ) : null}

          {isEdit && user ? (
            <div className="space-y-1">
              <p className="text-xs font-medium text-muted-foreground">{t("colUser")}</p>
              <p className="font-mono text-xs">{user.user_id}</p>
            </div>
          ) : null}

          <div className="space-y-2">
            <Label htmlFor="user-name">{t("fieldName")}</Label>
            <Input
              id="user-name"
              value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
              placeholder={t("fieldNamePlaceholder")}
              autoComplete="name"
            />
          </div>

          <div className="space-y-2">
            <Label htmlFor="user-email">{t("fieldEmail")}</Label>
            <Input
              id="user-email"
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder={t("fieldEmailPlaceholder")}
              autoComplete="email"
            />
            {!emailValid && email.trim().length > 0 ? (
              <p className="text-xs text-destructive">{t("fieldEmailInvalid")}</p>
            ) : null}
          </div>

          <div className="grid gap-4 sm:grid-cols-2">
            <div className="space-y-2">
              <Label htmlFor="user-telegram">{t("fieldTelegram")}</Label>
              <Input
                id="user-telegram"
                value={telegramUid}
                onChange={(e) => setTelegramUid(e.target.value)}
                placeholder={t("fieldTelegramPlaceholder")}
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="user-discord">{t("fieldDiscord")}</Label>
              <Input
                id="user-discord"
                value={discordUid}
                onChange={(e) => setDiscordUid(e.target.value)}
                placeholder={t("fieldDiscordPlaceholder")}
              />
            </div>
          </div>

          <div className="space-y-2">
            <p className="text-xs font-medium text-muted-foreground">{t("fieldAgents")}</p>
            {agentsLoading ? (
              <div className="flex flex-wrap gap-2">
                <Skeleton className="h-7 w-20 rounded-full" />
                <Skeleton className="h-7 w-20 rounded-full" />
              </div>
            ) : agentsError ? (
              <p className="text-xs text-destructive" role="alert">
                {t("fieldAgentsLoadError")}
              </p>
            ) : availableAgents.length > 0 ? (
              <div className="flex flex-wrap gap-2">
                {availableAgents.map((agent) => (
                  <FilterChip
                    key={agent}
                    label={agent}
                    active={selectedAgents.includes(agent)}
                    onToggle={() => toggleAgent(agent)}
                  />
                ))}
              </div>
            ) : (
              <p className="text-xs text-muted-foreground">{t("fieldAgentsEmpty")}</p>
            )}
          </div>

          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
              {t("formCancel")}
            </Button>
            <Button type="submit" disabled={!nameValid || !emailValid || saveMut.isPending}>
              {saveMut.isPending ? <Spinner className="mr-1.5" /> : null}
              {isEdit ? t("formSave") : t("formCreate")}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
