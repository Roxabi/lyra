import i18n from "i18next";
import { initReactI18next } from "react-i18next";
import enAdmin from "@/i18n/locales/en/admin.json";
import enAgents from "@/i18n/locales/en/agents.json";
import enChat from "@/i18n/locales/en/chat.json";
import enCommon from "@/i18n/locales/en/common.json";
import enDashboard from "@/i18n/locales/en/dashboard.json";
import enIntegrations from "@/i18n/locales/en/integrations.json";
import enJobs from "@/i18n/locales/en/jobs.json";
import enOps from "@/i18n/locales/en/ops.json";
import frAdmin from "@/i18n/locales/fr/admin.json";
import frAgents from "@/i18n/locales/fr/agents.json";
import frChat from "@/i18n/locales/fr/chat.json";
import frCommon from "@/i18n/locales/fr/common.json";
import frDashboard from "@/i18n/locales/fr/dashboard.json";
import frIntegrations from "@/i18n/locales/fr/integrations.json";
import frJobs from "@/i18n/locales/fr/jobs.json";
import frOps from "@/i18n/locales/fr/ops.json";

const STORAGE_KEY = "factory.dashboard.locale";

function readStoredLocale(): string {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored === "en" || stored === "fr") return stored;
  } catch {
    // private mode
  }
  return "fr";
}

void i18n.use(initReactI18next).init({
  resources: {
    fr: {
      common: frCommon,
      dashboard: frDashboard,
      jobs: frJobs,
      ops: frOps,
      chat: frChat,
      integrations: frIntegrations,
      agents: frAgents,
      admin: frAdmin,
    },
    en: {
      common: enCommon,
      dashboard: enDashboard,
      jobs: enJobs,
      ops: enOps,
      chat: enChat,
      integrations: enIntegrations,
      agents: enAgents,
      admin: enAdmin,
    },
  },
  lng: readStoredLocale(),
  fallbackLng: "fr",
  defaultNS: "common",
  interpolation: { escapeValue: false },
  pluralSeparator: "_",
});

export function persistLocale(locale: string) {
  try {
    localStorage.setItem(STORAGE_KEY, locale);
  } catch {
    // ignore
  }
}

export default i18n;
