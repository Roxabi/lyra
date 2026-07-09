import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/button";
import i18n, { persistLocale } from "@/i18n";

export function LocaleToggle() {
  const { t } = useTranslation("common");
  const locale = i18n.language === "en" ? "en" : "fr";
  const next = locale === "fr" ? "en" : "fr";

  return (
    <Button
      type="button"
      variant="outline"
      size="sm"
      aria-label={t("userMenu.language")}
      onClick={() => {
        void i18n.changeLanguage(next);
        persistLocale(next);
      }}
    >
      {next === "fr" ? t("userMenu.localeFr") : t("userMenu.localeEn")}
    </Button>
  );
}
