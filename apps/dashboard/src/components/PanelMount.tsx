interface PanelMountProps {
  id: string;
  title: string;
  disabled?: boolean;
}

export function PanelMount({ id, title, disabled = false }: PanelMountProps) {
  return (
    <div
      data-panel={id}
      className={`rounded-md border border-dashed border-border p-3 text-sm ${
        disabled ? "opacity-50" : ""
      }`}
    >
      <p className="font-[family-name:var(--font-head)] text-xs font-bold uppercase tracking-wide text-muted-foreground">
        {title}
      </p>
      <p className="mt-1 text-muted-foreground">
        {disabled ? "Coming in a future panel issue." : "Panel slot"}
      </p>
    </div>
  );
}
