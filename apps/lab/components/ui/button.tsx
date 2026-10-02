import type { ComponentProps } from "react";
import { LoaderCircle } from "lucide-react";

export type ButtonVariant = "primary" | "secondary" | "ghost" | "danger";
export type ButtonSize = "md" | "sm";

/** The button classes, for a Link or a Base UI part that must look like a Button. */
export function buttonClass(variant: ButtonVariant = "secondary", size: ButtonSize = "md"): string {
  return `lab-button lab-button--${variant}${size === "sm" ? " lab-button--sm" : ""}`;
}

/** type="button" unless stated; `pending` disables it (no double submit) and marks it busy. */
export function Button({
  variant = "secondary",
  size = "md",
  pending = false,
  type = "button",
  disabled,
  className,
  children,
  ...rest
}: ComponentProps<"button"> & { variant?: ButtonVariant; size?: ButtonSize; pending?: boolean }) {
  return (
    <button
      type={type}
      className={className ? `${buttonClass(variant, size)} ${className}` : buttonClass(variant, size)}
      disabled={disabled || pending}
      aria-busy={pending || undefined}
      {...rest}
    >
      {pending ? <LoaderCircle aria-hidden className="lab-spin" /> : null}
      {children}
    </button>
  );
}
