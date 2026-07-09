import { Send } from "lucide-react";
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
    <div className="border-t bg-card/40 px-4 py-3">
      <div className="mx-auto flex max-w-3xl items-end gap-2">
        <Textarea
          value={value}
          disabled={disabled}
          placeholder={disabled ? "Agent offline" : "Message…"}
          rows={1}
          className="max-h-32 min-h-[44px] flex-1 resize-none"
          onChange={(e) => onChange(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              onSend();
            }
          }}
        />
        <Button
          className="shrink-0 gap-1"
          disabled={disabled || !value.trim()}
          onClick={onSend}
          aria-label="Send"
        >
          <Send className="size-4" aria-hidden />
          Send
        </Button>
      </div>
    </div>
  );
}
