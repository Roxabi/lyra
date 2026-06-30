import { Toaster as Sonner, type ToasterProps } from "sonner";
import { useTheme } from "@/lib/use-theme";

export { toast } from "sonner";

export function Toaster(props: Omit<ToasterProps, "theme">) {
  const theme = useTheme();

  return (
    <Sonner
      theme={theme}
      position="bottom-center"
      offset="calc(env(safe-area-inset-bottom) + 16px)"
      toastOptions={{
        classNames: {
          toast:
            "bg-popover text-popover-foreground border border-border rounded-lg shadow-lg text-sm",
          title: "font-medium",
          description: "text-muted-foreground",
          actionButton: "bg-primary text-primary-foreground rounded-md",
          cancelButton: "bg-muted text-muted-foreground rounded-md",
          success: "text-success",
          error: "text-destructive",
        },
      }}
      {...props}
    />
  );
}
