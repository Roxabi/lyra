import { PaperPlaneRight } from "@phosphor-icons/react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";

interface ChatComposerProps {
  value: string;
  disabled: boolean;
  onChange: (value: string) => void;
  onSend: () => void;
}

export function ChatComposer({ value, disabled, onChange, onSend }: ChatComposerProps) {
  return (
    <div className="border-t border-border bg-card/40 px-4 py-3">
      <div className="mx-auto flex max-w-3xl items-end gap-2">
        <Textarea
          value={value}
          disabled={disabled}
          placeholder={disabled ? "Agent hors ligne" : "Écrivez votre message…"}
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
          aria-label="Envoyer"
        >
          <PaperPlaneRight className="size-4" weight="fill" aria-hidden />
          Envoyer
        </Button>
      </div>
    </div>
  );
}
