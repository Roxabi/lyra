import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Field, FieldError, FieldGroup, FieldLabel } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { changePassword } from "@/features/auth/api";
import { BffApiError } from "@/shared/api/client";

type Props = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
};

export function ChangePasswordDialog({ open, onOpenChange }: Props) {
  const { t } = useTranslation("auth");
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const [done, setDone] = useState(false);

  function reset() {
    setCurrentPassword("");
    setNewPassword("");
    setConfirm("");
    setError(null);
    setPending(false);
    setDone(false);
  }

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    if (newPassword.length < 8) {
      setError(t("password.minLength"));
      return;
    }
    if (newPassword !== confirm) {
      setError(t("password.mismatch"));
      return;
    }
    setPending(true);
    try {
      await changePassword(currentPassword, newPassword);
      setDone(true);
      setCurrentPassword("");
      setNewPassword("");
      setConfirm("");
    } catch (err) {
      setError(err instanceof BffApiError ? err.detail : t("password.failed"));
    } finally {
      setPending(false);
    }
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next) reset();
        onOpenChange(next);
      }}
    >
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{t("password.change")}</DialogTitle>
          <DialogDescription>{t("password.subtitle")}</DialogDescription>
        </DialogHeader>
        {done ? (
          <div className="space-y-4">
            <p className="text-sm text-muted-foreground">{t("password.success")}</p>
            <DialogFooter>
              <Button type="button" onClick={() => onOpenChange(false)}>
                {t("password.close")}
              </Button>
            </DialogFooter>
          </div>
        ) : (
          <form className="space-y-4" onSubmit={(e) => void onSubmit(e)}>
            <FieldGroup>
              <Field>
                <FieldLabel htmlFor="cur-pw">{t("password.current")}</FieldLabel>
                <Input
                  id="cur-pw"
                  type="password"
                  autoComplete="current-password"
                  value={currentPassword}
                  onChange={(e) => setCurrentPassword(e.target.value)}
                  required
                />
              </Field>
              <Field>
                <FieldLabel htmlFor="new-pw">{t("password.new")}</FieldLabel>
                <Input
                  id="new-pw"
                  type="password"
                  autoComplete="new-password"
                  value={newPassword}
                  onChange={(e) => setNewPassword(e.target.value)}
                  required
                  minLength={8}
                />
              </Field>
              <Field>
                <FieldLabel htmlFor="confirm-pw">{t("password.confirm")}</FieldLabel>
                <Input
                  id="confirm-pw"
                  type="password"
                  autoComplete="new-password"
                  value={confirm}
                  onChange={(e) => setConfirm(e.target.value)}
                  required
                  minLength={8}
                />
              </Field>
              {error ? <FieldError>{error}</FieldError> : null}
            </FieldGroup>
            <DialogFooter>
              <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
                {t("password.cancel")}
              </Button>
              <Button type="submit" disabled={pending}>
                {pending ? t("password.submitting") : t("password.submit")}
              </Button>
            </DialogFooter>
          </form>
        )}
      </DialogContent>
    </Dialog>
  );
}
