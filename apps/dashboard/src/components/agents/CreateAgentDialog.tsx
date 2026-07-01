import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { HarnessPicker } from "@/components/HarnessPicker";
import { ModelPicker } from "@/components/ModelPicker";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { toast } from "@/components/ui/sonner";
import { createAgentConfig } from "@/lib/agents-api";
import { bffErrorMessage } from "@/lib/bff-errors";
import type { HarnessKind } from "@/lib/chats-storage";

const SLUG_RE = /^[a-z][a-z0-9-]*$/;

interface CreateAgentDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export function CreateAgentDialog({ open, onOpenChange }: CreateAgentDialogProps) {
  const { t } = useTranslation("agents");
  const navigate = useNavigate();
  const qc = useQueryClient();

  const [name, setName] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [tagline, setTagline] = useState("");
  const [harness, setHarness] = useState<HarnessKind>("claude-cli");
  const [model, setModel] = useState("sonnet");

  const slug = name.trim().toLowerCase();
  const slugValid = slug.length > 0 && SLUG_RE.test(slug);

  const reset = () => {
    setName("");
    setDisplayName("");
    setTagline("");
    setHarness("claude-cli");
    setModel("sonnet");
  };

  const createMut = useMutation({
    mutationFn: () =>
      createAgentConfig({
        name: slug,
        backend: harness,
        model,
        display_name: displayName.trim() || slug,
        tagline: tagline.trim(),
      }),
    onSuccess: (cfg) => {
      toast.success(t("createSuccess", { name: cfg.name }));
      void qc.invalidateQueries({ queryKey: ["agents-config"] });
      onOpenChange(false);
      reset();
      void navigate({ to: "/agents/$name", params: { name: cfg.name } });
    },
    onError: (err) => {
      toast.error(bffErrorMessage(err, t, "create"));
    },
  });

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next) reset();
        onOpenChange(next);
      }}
      title={t("createTitle")}
      description={t("createDescription")}
    >
      <form
        className="space-y-4"
        onSubmit={(e) => {
          e.preventDefault();
          if (!slugValid || createMut.isPending) return;
          createMut.mutate();
        }}
      >
        <div className="space-y-2">
          <label className="text-xs font-medium text-muted-foreground" htmlFor="create-agent-name">
            {t("createName")}
          </label>
          <Input
            id="create-agent-name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder={t("createNamePlaceholder")}
            autoComplete="off"
            spellCheck={false}
            className="font-mono text-sm"
          />
          {!slugValid && slug.length > 0 ? (
            <p className="text-xs text-destructive">{t("createNameInvalid")}</p>
          ) : (
            <p className="text-xs text-muted-foreground">{t("createNameHint")}</p>
          )}
        </div>

        <div className="grid gap-4 sm:grid-cols-2">
          <div className="space-y-2">
            <label
              className="text-xs font-medium text-muted-foreground"
              htmlFor="create-agent-display"
            >
              {t("displayName")}
            </label>
            <Input
              id="create-agent-display"
              value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
            />
          </div>
          <div className="space-y-2">
            <label
              className="text-xs font-medium text-muted-foreground"
              htmlFor="create-agent-tagline"
            >
              {t("tagline")}
            </label>
            <Input
              id="create-agent-tagline"
              value={tagline}
              onChange={(e) => setTagline(e.target.value)}
            />
          </div>
        </div>

        <div className="flex flex-wrap gap-4">
          <HarnessPicker value={harness} onChange={setHarness} />
          <ModelPicker harness={harness} value={model} onChange={setModel} />
        </div>

        <div className="flex justify-end gap-2 pt-1">
          <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
            {t("createCancel")}
          </Button>
          <Button type="submit" disabled={!slugValid} loading={createMut.isPending}>
            {t("createSubmit")}
          </Button>
        </div>
      </form>
    </Dialog>
  );
}
