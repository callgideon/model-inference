"use client";

import type { ReactElement, ReactNode } from "react";
import { Dialog as Base } from "@base-ui/react/dialog";
import { X } from "lucide-react";
import { buttonClass } from "./button";

export type DialogProps = {
  title: ReactNode;
  description?: ReactNode;
  children: ReactNode;
  /** The element that opens it (a `<Button>`); focus returns to it on close. */
  trigger?: ReactElement;
  /** Controlled use; omit both for an uncontrolled dialog. */
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
  closeLabel?: string;
};

/**
 * The WAI modal dialog (Base UI): focus moves in, Tab stays inside, the background is inert,
 * Escape and the close button dismiss, focus returns to the trigger. Closed, its content is
 * unmounted, so nothing hidden is in the tab order.
 */
function Modal({ placement, title, description, children, trigger, open, onOpenChange, closeLabel = "Close" }: DialogProps & { placement: "center" | "start" }) {
  return (
    <Base.Root open={open} onOpenChange={onOpenChange ? (next) => onOpenChange(next) : undefined}>
      {trigger ? <Base.Trigger render={trigger} /> : null}
      <Base.Portal>
        <Base.Backdrop className="lab-dialog__backdrop" />
        <Base.Popup className={`lab-dialog lab-dialog--${placement}`}>
          <div className="lab-dialog__header">
            <Base.Title className="lab-dialog__title">{title}</Base.Title>
            <Base.Close className={buttonClass("ghost", "sm")} aria-label={closeLabel}>
              <X aria-hidden />
            </Base.Close>
          </div>
          {description ? <Base.Description className="lab-dialog__description">{description}</Base.Description> : null}
          {children}
        </Base.Popup>
      </Base.Portal>
    </Base.Root>
  );
}

export function Dialog(props: DialogProps) {
  return <Modal placement="center" {...props} />;
}

/** The mobile navigation drawer: the same modal, at the start edge. */
export function Drawer(props: DialogProps) {
  return <Modal placement="start" {...props} />;
}
