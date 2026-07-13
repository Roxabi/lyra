/**
 * Login page — layout aligned with shadcn block login-05
 * (centered stack, brand mark, FieldGroup form; password kept for hub IdP).
 */
import { Link, useNavigate, useSearch } from "@tanstack/react-router";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/button";
import { Field, FieldDescription, FieldError, FieldGroup, FieldLabel } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { useAuth } from "@/features/auth/auth-context";
import { cn } from "@/lib/utils";
import { BffApiError } from "@/shared/api/client";

export function LoginPage() {
  const { t } = useTranslation("auth");
  const { login, status } = useAuth();
  const navigate = useNavigate();
  const search = useSearch({ strict: false }) as { redirect?: string };
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  if (status === "authenticated" || status === "legacy") {
    void navigate({ to: (search.redirect as "/") || "/" });
  }

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setPending(true);
    try {
      await login(email.trim(), password);
      await navigate({ to: search.redirect || "/" });
    } catch (err) {
      setError(err instanceof BffApiError ? err.detail : t("login.failed"));
    } finally {
      setPending(false);
    }
  }

  return (
    <div className="flex min-h-svh flex-col items-center justify-center gap-6 bg-background p-6 md:p-10">
      <div className="w-full max-w-sm">
        <div className={cn("flex flex-col gap-6")}>
          <form onSubmit={(e) => void onSubmit(e)}>
            <FieldGroup>
              <div className="flex flex-col items-center gap-2 text-center">
                <div className="flex flex-col items-center gap-2 font-medium">
                  <div className="flex size-10 items-center justify-center rounded-md">
                    <img
                      src="/factory-mark.svg"
                      alt=""
                      width={40}
                      height={40}
                      className="size-10"
                    />
                  </div>
                  <span className="sr-only">{t("login.brand")}</span>
                </div>
                <h1 className="text-xl font-bold">{t("login.welcome")}</h1>
                <FieldDescription className="text-center">
                  {t("login.subtitle")}{" "}
                  <Link
                    to="/accept-invite"
                    search={{ token: undefined }}
                    className="underline underline-offset-4"
                  >
                    {t("login.acceptInvite")}
                  </Link>
                </FieldDescription>
              </div>

              <Field>
                <FieldLabel htmlFor="email">{t("login.email")}</FieldLabel>
                <Input
                  id="email"
                  type="email"
                  placeholder={t("login.emailPlaceholder")}
                  autoComplete="username"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  required
                />
              </Field>

              <Field>
                <FieldLabel htmlFor="password">{t("login.password")}</FieldLabel>
                <Input
                  id="password"
                  type="password"
                  autoComplete="current-password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  required
                />
              </Field>

              {error ? <FieldError>{error}</FieldError> : null}

              <Field>
                <Button type="submit" className="w-full" disabled={pending}>
                  {pending ? t("login.submitting") : t("login.submit")}
                </Button>
              </Field>
            </FieldGroup>
          </form>
        </div>
      </div>
    </div>
  );
}
