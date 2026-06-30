import { PaperPlaneRight } from "@phosphor-icons/react";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";

interface ChatComposerProps {
  value: string;
  disabled: boolean;
  onChange: (value: string) => void;
  onSend: () => void;
}

export function ChatComposer({ value, disabled, onChange, onSend }: ChatComposerProps) {
  const { t } = useTranslation("chat");
  return (
    <div className="border-t border-border bg-card/40 px-4 py-3">
      <div className="mx-auto flex max-w-3xl items-end gap-2">
        <Textarea
          value={value}
          disabled={disabled}
          placeholder={disabled ? t("composer.placeholderOffline") : t("composer.placeholder")}
          rows={1}
          className="min-h-[44px] max-h-32 flex-1"
          onChange={(e) => onChange(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              onSend();
            }
          }}
        />
        <Button
          className="shrink-0"
          disabled={disabled || !value.trim()}
          onClick={onSend}
          aria-label={t("composer.send")}
        >
          <PaperPlaneRight className="size-4" weight="fill" aria-hidden />
          {t("composer.send")}
        </Button>
      </div>
    </div>
  );
}
