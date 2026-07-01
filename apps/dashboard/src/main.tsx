import { Theme } from "@astryxdesign/core/theme";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider } from "@tanstack/react-router";
import { type ReactNode, StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { I18nextProvider } from "react-i18next";
import "./index.css";
import { roxabiTheme } from "@/astryx-theme/built/roxabi";
import i18n from "@/i18n";
import { useTheme } from "@/lib/use-theme";
import { router } from "@/router";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      retry: 1,
    },
  },
});

/**
 * Astryx theme provider — applies the brand `roxabi` theme (#2089) and is the
 * SOLE runtime owner of `<html>`'s `data-theme` + `color-scheme`. The dashboard
 * preference lives in `src/lib/theme.ts` (state only: persisted pref + change
 * event); `useTheme()` mirrors it into `<Theme mode>`, which writes `data-theme`
 * on the document root. A pre-paint bootstrap in `index.html` sets the initial
 * value to avoid FOUC before React hydrates.
 */
function AstryxThemeProvider({ children }: { children: ReactNode }) {
  const mode = useTheme();
  return (
    <Theme theme={roxabiTheme} mode={mode}>
      {children}
    </Theme>
  );
}

const rootEl = document.getElementById("root");
if (!rootEl) throw new Error("Root element #root not found");

createRoot(rootEl).render(
  <StrictMode>
    <AstryxThemeProvider>
      <I18nextProvider i18n={i18n}>
        <QueryClientProvider client={queryClient}>
          <RouterProvider router={router} context={{ queryClient }} />
        </QueryClientProvider>
      </I18nextProvider>
    </AstryxThemeProvider>
  </StrictMode>,
);
