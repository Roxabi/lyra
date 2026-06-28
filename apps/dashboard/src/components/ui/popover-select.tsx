import { CaretDown, Check } from "@phosphor-icons/react";
import { useEffect, useId, useRef, useState } from "react";
import { cn } from "@/lib/utils";

export interface PopoverOption {
  value: string;
  label: string;
  hint?: string;
  disabled?: boolean;
}

interface PopoverSelectProps {
  label: string;
  value: string;
  options: PopoverOption[];
  disabled?: boolean;
  onChange: (value: string) => void;
  className?: string;
}

export function PopoverSelect({
  label,
  value,
  options,
  disabled,
  onChange,
  className,
}: PopoverSelectProps) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const listId = useId();
  const selected = options.find((o) => o.value === value);

  useEffect(() => {
    if (!open) return;
    const onDoc = (ev: MouseEvent) => {
      if (!rootRef.current?.contains(ev.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [open]);

  return (
    <div ref={rootRef} className={cn("relative", className)}>
      <button
        type="button"
        disabled={disabled}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={listId}
        className={cn(
          "inline-flex h-8 min-w-[7rem] items-center justify-between gap-2 rounded-lg bg-muted/50 px-2.5 text-xs font-medium text-foreground transition-colors hover:bg-muted disabled:cursor-not-allowed disabled:opacity-50",
        )}
        onClick={() => setOpen((v) => !v)}
      >
        <span className="truncate">{selected?.label ?? label}</span>
        <CaretDown className={cn("size-3 shrink-0 text-muted-foreground", open && "rotate-180")} />
      </button>
      {open ? (
        <div
          id={listId}
          role="listbox"
          aria-label={label}
          className="absolute right-0 z-50 mt-1 max-h-56 min-w-full overflow-auto rounded-lg bg-popover p-1 shadow-panel"
        >
          {options.map((opt) => (
            <div key={opt.value}>
              <button
                type="button"
                role="option"
                aria-selected={opt.value === value}
                disabled={opt.disabled}
                className={cn(
                  "flex w-full items-center justify-between gap-2 rounded-md px-2.5 py-2 text-left text-xs transition-colors hover:bg-muted/80 disabled:opacity-40",
                  opt.value === value && "bg-primary/10 text-foreground",
                )}
                onMouseDown={(e) => e.preventDefault()}
                onClick={() => {
                  onChange(opt.value);
                  setOpen(false);
                }}
              >
                <span>
                  <span className="block font-medium">{opt.label}</span>
                  {opt.hint ? (
                    <span className="block text-[10px] text-muted-foreground">{opt.hint}</span>
                  ) : null}
                </span>
                {opt.value === value ? <Check className="size-3 text-brand" weight="bold" /> : null}
              </button>
            </div>
          ))}
        </div>
      ) : null}
    </div>
  );
}
