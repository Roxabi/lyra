interface PanelMountProps {
  id: string;
  title: string;
  disabled?: boolean;
}

export function PanelMount({ id, title, disabled = false }: PanelMountProps) {
  return (
    <div
      data-panel={id}
      className={`rounded-lg border border-border/70 bg-background/50 px-3 py-2.5 ${
        disabled ? "opacity-70" : ""
      }`}
    >
      <div className="flex items-center justify-between gap-2">
        <p className="text-xs font-medium text-foreground">{title}</p>
        {disabled ? (
          <span className="rounded-full bg-muted px-2 py-0.5 text-[10px] text-muted-foreground">
            Bientôt
          </span>
        ) : null}
      </div>
      {disabled ? (
        <p className="mt-1 text-[11px] text-muted-foreground">Coming in a future panel issue.</p>
      ) : (
        <p className="mt-1 text-[11px] text-muted-foreground">Panel slot</p>
      )}
    </div>
  );
}
