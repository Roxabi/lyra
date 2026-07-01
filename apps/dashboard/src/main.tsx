import { Theme } from "@astryxdesign/core/theme";
import { neutralTheme } from "@astryxdesign/theme-neutral/built";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider } from "@tanstack/react-router";
import { type ReactNode, StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { I18nextProvider } from "react-i18next";
import "./index.css";
import i18n from "@/i18n";
import { initTheme } from "@/lib/theme";
import { useTheme } from "@/lib/use-theme";
import { router } from "@/router";

initTheme();

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      retry: 1,
    },
  },
});

/**
 * Astryx theme provider. Bridges the dashboard's own dark/light system
 * (src/lib/theme.ts → data-theme + `factory-dashboard:theme-change` event) into
 * Astryx's <Theme mode>, so Astryx components follow the same toggle rather than
 * the OS preference. Full token reconciliation (custom `roxabi` theme) lands in
 * slice 1 (#2089); S0 uses the neutral placeholder theme.
 */
function AstryxThemeProvider({ children }: { children: ReactNode }) {
  const mode = useTheme();
  return (
    <Theme theme={neutralTheme} mode={mode}>
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
