import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { useState } from "react";
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
import { Spinner } from "@/components/ui/spinner";
import { createAgentConfig } from "@/features/agents/api";
import { HarnessPicker } from "@/shared/components/harness-picker";
import { ModelPicker } from "@/shared/components/model-picker";
import { bffErrorMessage } from "@/shared/lib/bff-errors";
import type { HarnessKind } from "@/shared/lib/chats-storage";

const SLUG_RE = /^[a-z][a-z0-9-]*$/;

interface CreateAgentDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export function CreateAgentDialog({ open, onOpenChange }: CreateAgentDialogProps) {
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
      toast.info(`Agent "${cfg.name}" created.`);
      void qc.invalidateQueries({ queryKey: ["agents-config"] });
      onOpenChange(false);
      reset();
      void navigate({ to: "/agents/$name", params: { name: cfg.name } });
    },
  });

  const handleOpenChange = (next: boolean) => {
    if (!next) reset();
    onOpenChange(next);
  };

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Create agent</DialogTitle>
          <DialogDescription>
            Add a new agent to the factory roster. The slug is used in API paths and storage keys.
          </DialogDescription>
        </DialogHeader>

        <form
          className="space-y-4"
          onSubmit={(e) => {
            e.preventDefault();
            if (!slugValid || createMut.isPending) return;
            createMut.mutate();
          }}
        >
          {createMut.isError ? (
            <Alert variant="destructive">
              <AlertDescription>
                {bffErrorMessage(createMut.error, "Failed to create agent.")}
              </AlertDescription>
            </Alert>
          ) : null}

          <div className="space-y-2">
            <Label htmlFor="agent-slug">Slug</Label>
            <Input
              id="agent-slug"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="my-agent"
              autoComplete="off"
            />
            {!slugValid && slug.length > 0 ? (
              <p className="text-xs text-destructive">
                Lowercase letters, numbers, and hyphens only. Must start with a letter.
              </p>
            ) : (
              <p className="text-xs text-muted-foreground">
                Used in URLs and config keys (e.g. lyra, field-bot).
              </p>
            )}
          </div>

          <div className="grid gap-4 sm:grid-cols-2">
            <div className="space-y-2">
              <Label htmlFor="agent-display-name">Display name</Label>
              <Input
                id="agent-display-name"
                value={displayName}
                onChange={(e) => setDisplayName(e.target.value)}
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="agent-tagline">Tagline</Label>
              <Input
                id="agent-tagline"
                value={tagline}
                onChange={(e) => setTagline(e.target.value)}
              />
            </div>
          </div>

          <div className="flex flex-wrap gap-4">
            <HarnessPicker value={harness} onChange={setHarness} />
            <ModelPicker harness={harness} value={model} onChange={setModel} />
          </div>

          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button type="submit" disabled={!slugValid || createMut.isPending}>
              {createMut.isPending ? <Spinner className="mr-1.5" /> : null}
              Create
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
