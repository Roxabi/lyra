import { Moon, Sun } from "@phosphor-icons/react";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/button";
import { applyTheme } from "@/lib/theme";
import { useTheme } from "@/lib/use-theme";
import { cn } from "@/lib/utils";

interface ThemeToggleProps {
  className?: string;
}

export function ThemeToggle({ className }: ThemeToggleProps) {
  const { t } = useTranslation();
  // Read from the shared reactive hook (subscribes to THEME_CHANGE_EVENT) so
  // the icon stays in sync when the theme changes via any other control.
  const theme = useTheme();
  const Icon = theme === "dark" ? Moon : Sun;

  const toggle = () => {
    applyTheme(theme === "dark" ? "light" : "dark");
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
