import type { ReactNode } from "react";
import { Field as Base } from "@base-ui/react/field";

/**
 * A labelled control: Base UI ties the label, description and error to the control (htmlFor,
 * aria-describedby) and marks it aria-invalid while `error` is set. Show `error` only after the
 * user interacted or submitted (02-foundations); never reset the value on an error.
 */
export function Field({
  label,
  description,
  error,
  name,
  children,
}: {
  label: ReactNode;
  description?: ReactNode;
  error?: string | null;
  name?: string;
  /** One `<Input>` (or `<Input render={<textarea />} />` / a select). */
  children: ReactNode;
}) {
  return (
    <Base.Root className="lab-field" name={name} invalid={Boolean(error)}>
      <Base.Label className="lab-field__label">{label}</Base.Label>
      {children}
      {description ? <Base.Description className="lab-field__description">{description}</Base.Description> : null}
      {error ? (
        <Base.Error className="lab-field__error" match>
          {error}
        </Base.Error>
      ) : null}
    </Base.Root>
  );
}

export function Input({ className, ...props }: Base.Control.Props) {
  return <Base.Control className={className ? `lab-input ${className}` : "lab-input"} {...props} />;
}
