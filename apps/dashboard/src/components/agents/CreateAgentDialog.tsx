import { Dialog, DialogHeader } from "@astryxdesign/core/Dialog";
import { TextInput } from "@astryxdesign/core/TextInput";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { HarnessPicker } from "@/components/HarnessPicker";
import { ModelPicker } from "@/components/ModelPicker";
import { Button } from "@/components/ui/button";
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

  const handleOpenChange = (next: boolean) => {
    if (!next) reset();
    onOpenChange(next);
  };

  return (
    // Kept mounted (isOpen toggles) so Astryx's close effect runs its
    // focus-restore-to-opener — unmounting on close would skip it (no cleanup).
    // `aria-label` names the modal (Astryx doesn't wire aria-labelledby→title).
    <Dialog
      isOpen={open}
      onOpenChange={handleOpenChange}
      purpose="form"
      width="28rem"
      aria-label={t("createTitle")}
    >
      <DialogHeader
        title={t("createTitle")}
        subtitle={t("createDescription")}
        onOpenChange={handleOpenChange}
      />
      <form
        className="space-y-4"
        onSubmit={(e) => {
          e.preventDefault();
          if (!slugValid || createMut.isPending) return;
          createMut.mutate();
        }}
      >
        <TextInput
          label={t("createName")}
          value={name}
          onChange={(v) => setName(v)}
          placeholder={t("createNamePlaceholder")}
          width="100%"
          description={slugValid || slug.length === 0 ? t("createNameHint") : undefined}
          status={
            !slugValid && slug.length > 0
              ? { type: "error", message: t("createNameInvalid") }
              : undefined
          }
        />

        <div className="grid gap-4 sm:grid-cols-2">
          <TextInput
            label={t("displayName")}
            value={displayName}
            onChange={(v) => setDisplayName(v)}
            width="100%"
          />
          <TextInput
            label={t("tagline")}
            value={tagline}
            onChange={(v) => setTagline(v)}
            width="100%"
          />
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
