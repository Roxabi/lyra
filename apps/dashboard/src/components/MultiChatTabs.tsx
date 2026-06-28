import { Plus, X } from "@phosphor-icons/react";
import { Button } from "@/components/ui/button";
import type { ChatTab } from "@/lib/chats-storage";
import { cn } from "@/lib/utils";

interface MultiChatTabsProps {
  tabs: ChatTab[];
  activeId: string | null;
  onSelect: (id: string) => void;
  onClose: (id: string) => void;
  onNew: () => void;
}

export function MultiChatTabs({ tabs, activeId, onSelect, onClose, onNew }: MultiChatTabsProps) {
  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="border-b border-border px-3 py-3">
        <Button variant="outline" size="sm" className="w-full justify-start gap-2" onClick={onNew}>
          <Plus className="size-4" aria-hidden />+ New chat
        </Button>
      </div>
      <ul className="fd-scroll flex min-h-0 flex-1 flex-col gap-0.5 overflow-y-auto p-2">
        {tabs.map((tab) => {
          const active = tab.id === activeId;
          return (
            <li key={tab.id}>
              <div
                className={cn(
                  "group flex items-center gap-1 rounded-lg transition-colors",
                  active ? "bg-primary/10" : "hover:bg-muted/60",
                )}
              >
                <button
                  type="button"
                  className="flex min-w-0 flex-1 items-center gap-2 px-2.5 py-2 text-left"
                  onClick={() => onSelect(tab.id)}
                >
                  <span
                    className={cn(
                      "flex size-6 shrink-0 items-center justify-center rounded-md text-[10px] font-semibold uppercase",
                      active
                        ? "bg-brand/20 text-brand"
                        : "border border-border bg-background text-muted-foreground",
                    )}
                    aria-hidden
                  >
                    {tab.agent.slice(0, 1)}
                  </span>
                  <span
                    className={cn(
                      "truncate text-sm",
                      active ? "font-medium text-foreground" : "text-muted-foreground",
                    )}
                  >
                    {tab.agent}
                  </span>
                </button>
                <button
                  type="button"
                  className="mr-1 rounded p-1 text-muted-foreground opacity-0 transition-opacity hover:bg-background hover:text-foreground group-hover:opacity-100"
                  aria-label="Close tab"
                  onClick={() => onClose(tab.id)}
                >
                  <X className="size-3.5" aria-hidden />
                </button>
              </div>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
