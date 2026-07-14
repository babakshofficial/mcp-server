import * as React from "react";

import { cn } from "../../lib/utils";

export function Button({
  className,
  variant = "default",
  ...props
}: React.ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "default" | "outline" }) {
  return (
    <button
      className={cn(
        "inline-flex h-10 items-center justify-center rounded-md px-4 py-2 text-sm font-medium shadow transition-colors disabled:pointer-events-none disabled:opacity-50",
        variant === "outline"
          ? "border bg-background text-foreground hover:bg-muted"
          : "bg-primary text-primary-foreground hover:opacity-90",
        className
      )}
      {...props}
    />
  );
}
