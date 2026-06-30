import { useEffect, useState } from "react";
import { readTheme, THEME_CHANGE_EVENT, type Theme } from "@/lib/theme";

export function useTheme(): Theme {
  const [theme, setTheme] = useState<Theme>(readTheme);

  useEffect(() => {
    const onThemeChange = (event: Event) => {
      const detail = (event as CustomEvent<Theme>).detail;
      if (detail) setTheme(detail);
    };
    window.addEventListener(THEME_CHANGE_EVENT, onThemeChange);
    return () => window.removeEventListener(THEME_CHANGE_EVENT, onThemeChange);
  }, []);

  return theme;
}
