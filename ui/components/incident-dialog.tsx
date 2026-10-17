"use client";

import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { Button } from "@/components/app/kit";

/**
 * Confirmation dialog on the native <dialog> (focus trap, Escape, top layer for free).
 * With `reason`, a textarea is shown and confirming needs non-blank text (`reasonRequired`).
 */
export function ConfirmDialog({
  open,
  title,
  children,
  confirmLabel,
  danger,
  reason,
  reasonRequired,
  busy,
  error,
  onConfirm,
  onClose,
}: {
  open: boolean;
  title: string;
  children?: ReactNode;
  confirmLabel: string;
  danger?: boolean;
  reason?: string; // textarea label; omit for no textarea
  reasonRequired?: boolean;
  busy?: boolean;
  error?: string | null;
  onConfirm: (reason: string) => void;
  onClose: () => void;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const [text, setText] = useState("");
  const [touched, setTouched] = useState(false);
  const id = useId();

  useEffect(() => {
    const d = ref.current;
    if (!d) return;
    if (open && !d.open) {
      setText("");
      setTouched(false);
      d.showModal();
    } else if (!open && d.open) d.close();
  }, [open]);

  const missing = !!reasonRequired && !text.trim();
  return (
    <dialog
      ref={ref}
      aria-labelledby={`${id}-t`}
      onClose={onClose}
      onCancel={onClose}
      className="m-auto w-[min(440px,calc(100vw-32px))] rounded-xl border border-line bg-raised p-0 text-ink shadow-[var(--shadow-pop)] backdrop:bg-black/50"
    >
      <form
        method="dialog"
        className="flex flex-col gap-3 p-5"
        onSubmit={(e) => {
          e.preventDefault();
          setTouched(true);
          if (missing || busy) return;
          onConfirm(text.trim());
        }}
      >
        <h2 id={`${id}-t`} className="text-[15px] font-semibold">
          {title}
        </h2>
        {children && <div className="text-[13px] leading-[1.55] text-ink-2">{children}</div>}
        {reason && (
          <label className="flex flex-col gap-1.5 text-[12.5px] text-ink-2">
            {reason}
            {reasonRequired ? " (required)" : ""}
            <textarea
              value={text}
              onChange={(e) => setText(e.target.value)}
              rows={3}
              maxLength={2000}
              aria-invalid={touched && missing}
              aria-describedby={touched && missing ? `${id}-e` : undefined}
              className="rounded-md border border-line bg-panel-2 px-2.5 py-2 text-[13px] text-ink outline-none focus:border-accent"
            />
          </label>
        )}
        {touched && missing && (
          <p id={`${id}-e`} role="alert" className="text-[12.5px] text-red">
            A reason is required.
          </p>
        )}
        {error && (
          <p role="alert" className="text-[12.5px] text-red">
            {error}
          </p>
        )}
        <div className="mt-1 flex justify-end gap-2">
          <Button onClick={onClose}>Cancel</Button>
          <button
            type="submit"
            disabled={busy}
            className={
              "inline-flex h-7 items-center rounded-md px-2.5 text-[12.5px] font-medium disabled:opacity-45 " +
              (danger ? "bg-red text-white hover:opacity-90" : "bg-primary text-on-primary hover:opacity-90")
            }
          >
            {busy ? "Working…" : confirmLabel}
          </button>
        </div>
      </form>
    </dialog>
  );
}
