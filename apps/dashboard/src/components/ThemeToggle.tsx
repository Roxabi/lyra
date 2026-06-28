import { Moon, Sun } from "@phosphor-icons/react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/button";
import { applyTheme, readTheme, type Theme } from "@/lib/theme";
import { cn } from "@/lib/utils";

interface ThemeToggleProps {
  variant?: "icon" | "segment";
  className?: string;
}

export function ThemeToggle({ variant = "icon", className }: ThemeToggleProps) {
  const { t } = useTranslation();
  const [theme, setTheme] = useState<Theme>(readTheme);

  const setAndApply = (next: Theme) => {
    applyTheme(next);
    setTheme(next);
  };

  if (variant === "segment") {
    return (
      <div
        role="group"
        aria-label={t("theme.toggle")}
        className={cn("grid grid-cols-2 gap-1 rounded-lg bg-muted p-1", className)}
      >
        {(["light", "dark"] as const).map((value) => {
          const active = theme === value;
          const Icon = value === "light" ? Sun : Moon;
          return (
            <button
              key={value}
              type="button"
              aria-pressed={active}
              title={t(`theme.${value}`)}
              onClick={() => setAndApply(value)}
              className={cn(
                "inline-flex min-h-9 items-center justify-center gap-1 rounded-md px-2 text-xs font-medium transition-colors",
                "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2",
                active
                  ? "bg-background text-foreground shadow-sm"
                  : "text-muted-foreground hover:text-foreground",
              )}
            >
              <Icon className="size-4" aria-hidden />
              <span>{t(`theme.${value}`)}</span>
            </button>
          );
        })}
      </div>
    );
  }

  return (
    <Button
      variant="ghost"
      size="icon"
      className={className}
      aria-label={t("theme.toggle")}
      onClick={() => setAndApply(theme === "dark" ? "light" : "dark")}
    >
      {theme === "dark" ? <Sun className="size-5" /> : <Moon className="size-5" />}
    </Button>
  );
}