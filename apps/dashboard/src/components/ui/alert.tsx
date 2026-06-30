import { cva, type VariantProps } from "class-variance-authority";
import type * as React from "react";
import { cn } from "@/lib/utils";

const alertVariants = cva("rounded-md border px-3 py-2 text-sm", {
  variants: {
    variant: {
      destructive: "border-destructive/40 bg-destructive/10 text-destructive",
      warning: "border-status-closing/40 bg-status-closing/10 text-foreground",
      info: "border-primary/40 bg-primary/10 text-foreground",
      success: "border-status-open/40 bg-status-open/10 text-foreground",
    },
  },
  defaultVariants: {
    variant: "info",
  },
});

export interface AlertProps
  extends React.HTMLAttributes<HTMLDivElement>,
    VariantProps<typeof alertVariants> {}

function Alert({ className, variant, role, ...props }: AlertProps) {
  const defaultRole =
    variant === "destructive" ? "alert" : variant === "success" ? "status" : undefined;
  return (
    <div
      role={role ?? defaultRole}
      className={cn(alertVariants({ variant }), className)}
      {...props}
    />
  );
}

export { Alert, alertVariants };