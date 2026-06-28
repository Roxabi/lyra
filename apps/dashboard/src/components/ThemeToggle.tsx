import { Moon, Sun } from "@phosphor-icons/react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/button";
import { applyTheme, readTheme, type Theme } from "@/lib/theme";
import { cn } from "@/lib/utils";

interface ThemeToggleProps {
  className?: string;
}

export function ThemeToggle({ className }: ThemeToggleProps) {
  const { t } = useTranslation();
  const [theme, setTheme] = useState<Theme>(readTheme);
  const Icon = theme === "dark" ? Moon : Sun;

  const toggle = () => {
    const next: Theme = theme === "dark" ? "light" : "dark";
    applyTheme(next);
    setTheme(next);
  };

  return (
    <Button
      variant="ghost"
      size="icon"
      className={cn(className)}
      aria-label={t("theme.toggle")}
      title={t(`theme.${theme}`)}
      onClick={toggle}
    >
      <Icon className="size-5" aria-hidden />
    </Button>
  );
}
