import { Link, useNavigate, useSearch } from "@tanstack/react-router";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { acceptInvite } from "@/features/auth/api";
import { useAuth } from "@/features/auth/auth-context";
import { BffApiError } from "@/shared/api/client";

export function AcceptInvitePage() {
  const { t } = useTranslation("auth");
  const { refresh } = useAuth();
  const navigate = useNavigate();
  const search = useSearch({ strict: false }) as { token?: string };
  const [token, setToken] = useState(search.token ?? "");
  const [password, setPassword] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setPending(true);
    try {
      await acceptInvite({
        token: token.trim(),
        password,
        display_name: displayName.trim() || undefined,
      });
      await refresh();
      await navigate({ to: "/account/links" });
    } catch (err) {
      setError(err instanceof BffApiError ? err.detail : t("invite.failed"));
    } finally {
      setPending(false);
    }
  }

  return (
    <div className="flex min-h-dvh items-center justify-center bg-background p-4">
      <Card className="w-full max-w-md">
        <CardHeader>
          <CardTitle className="font-heading text-2xl">{t("invite.title")}</CardTitle>
          <CardDescription>{t("invite.subtitle")}</CardDescription>
        </CardHeader>
        <CardContent>
          <form className="space-y-4" onSubmit={(e) => void onSubmit(e)}>
            <div className="space-y-2">
              <Label htmlFor="token">{t("invite.token")}</Label>
              <Input
                id="token"
                value={token}
                onChange={(e) => setToken(e.target.value)}
                required
                autoComplete="off"
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="displayName">{t("invite.displayName")}</Label>
              <Input
                id="displayName"
                value={displayName}
                onChange={(e) => setDisplayName(e.target.value)}
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="password">{t("invite.password")}</Label>
              <Input
                id="password"
                type="password"
                minLength={8}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
              />
            </div>
            {error ? (
              <p className="text-sm text-destructive" role="alert">
                {error}
              </p>
            ) : null}
            <Button type="submit" className="w-full" disabled={pending}>
              {pending ? t("invite.submitting") : t("invite.submit")}
            </Button>
          </form>
          <p className="mt-4 text-center text-xs text-muted-foreground">
            <Link
              to="/login"
              search={{ redirect: undefined }}
              className="underline underline-offset-2"
            >
              {t("invite.backLogin")}
            </Link>
          </p>
        </CardContent>
      </Card>
    </div>
  );
}
