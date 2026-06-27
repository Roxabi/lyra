import { Button } from "@/components/ui/button";
import type { ChatTab } from "@/lib/chats-storage";

interface MultiChatTabsProps {
  tabs: ChatTab[];
  activeId: string | null;
  onSelect: (id: string) => void;
  onClose: (id: string) => void;
  onNew: () => void;
}

export function MultiChatTabs({ tabs, activeId, onSelect, onClose, onNew }: MultiChatTabsProps) {
  return (
    <div className="flex h-full min-h-0 flex-col gap-2">
      <Button variant="outline" size="sm" className="w-full" onClick={onNew}>
        + New chat
      </Button>
      <ul className="flex min-h-0 flex-1 flex-col gap-1 overflow-auto">
        {tabs.map((tab) => (
          <li key={tab.id} className="flex items-center gap-1">
            <button
              type="button"
              className={`flex-1 truncate rounded px-2 py-1.5 text-left text-xs ${
                tab.id === activeId ? "bg-primary/15 text-foreground" : "hover:bg-muted"
              }`}
              onClick={() => onSelect(tab.id)}
            >
              {tab.agent}
            </button>
            <button
              type="button"
              className="px-1 text-muted-foreground hover:text-foreground"
              aria-label="Close tab"
              onClick={() => onClose(tab.id)}
            >
              ×
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}
