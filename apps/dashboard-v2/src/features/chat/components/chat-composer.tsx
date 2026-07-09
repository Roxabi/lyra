import { Send, Square } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";

interface ChatComposerProps {
  value: string;
  disabled: boolean;
  isLoading?: boolean;
  onChange: (value: string) => void;
  onSend: () => void;
  onStop?: () => void;
}

export function ChatComposer({
  value,
  disabled,
  isLoading = false,
  onChange,
  onSend,
  onStop,
}: ChatComposerProps) {
  const { t } = useTranslation("chat");
  const busy = disabled || isLoading;

  return (
    <div className="border-t bg-card/40 px-4 py-3">
      <div className="mx-auto flex max-w-3xl items-end gap-2">
        <Textarea
          value={value}
          disabled={busy}
          placeholder={disabled ? t("composer.placeholderOffline") : t("composer.placeholder")}
          rows={1}
          className="max-h-32 min-h-[44px] flex-1 resize-none"
          onChange={(e) => onChange(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              if (!busy && value.trim()) onSend();
            }
          }}
        />
        {isLoading && onStop ? (
          <Button
            type="button"
            variant="outline"
            className="shrink-0 gap-1"
            onClick={onStop}
            aria-label={t("composer.stop")}
          >
            <Square className="size-4" aria-hidden />
            {t("composer.stop")}
          </Button>
        ) : (
          <Button
            className="shrink-0 gap-1"
            disabled={busy || !value.trim()}
            onClick={onSend}
            aria-label={t("composer.send")}
          >
            <Send className="size-4" aria-hidden />
            {t("composer.send")}
          </Button>
        )}
      </div>
    </div>
  );
}
