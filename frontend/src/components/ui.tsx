/** Small, consistent UI primitives built on Radix for accessibility. */
import * as DialogPrimitive from "@radix-ui/react-dialog";
import * as Menu from "@radix-ui/react-dropdown-menu";
import * as TooltipPrimitive from "@radix-ui/react-tooltip";
import { Loader2, X } from "lucide-react";
import { forwardRef, useId, useState, type ButtonHTMLAttributes, type InputHTMLAttributes, type ReactNode, type TextareaHTMLAttributes } from "react";
import { cn } from "@/lib/utils";
import type { ProviderStatus } from "@/lib/types";

/* ---------------------------------------------------------------- Button */
type Variant = "primary" | "secondary" | "ghost" | "danger" | "outline";
type Size = "sm" | "md";

export const Button = forwardRef<
  HTMLButtonElement,
  ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant; size?: Size; loading?: boolean; icon?: ReactNode }
>(({ variant = "secondary", size = "md", loading, icon, className, children, disabled, ...props }, ref) => (
  <button
    ref={ref}
    disabled={disabled || loading}
    className={cn(
      "inline-flex shrink-0 items-center justify-center gap-1.5 rounded-lg font-medium whitespace-nowrap transition-colors duration-150 select-none disabled:opacity-50",
      size === "sm" ? "h-8 px-2.5 text-[0.8125rem]" : "h-9 px-3.5 text-sm",
      variant === "primary" && "bg-fg text-bg hover:opacity-90",
      variant === "secondary" && "bg-subtle text-fg hover:bg-active",
      variant === "outline" && "border border-line-strong text-fg hover:bg-hover",
      variant === "ghost" && "text-muted hover:bg-hover hover:text-fg",
      variant === "danger" && "bg-danger text-white hover:opacity-90",
      className,
    )}
    {...props}
  >
    {loading ? <Loader2 className="size-4 animate-spin" aria-hidden /> : icon}
    {children}
  </button>
));
Button.displayName = "Button";

/* ---------------------------------------------------------------- Tooltip */
export function TooltipProvider({ children }: { children: ReactNode }) {
  return (
    <TooltipPrimitive.Provider delayDuration={450} skipDelayDuration={200}>
      {children}
    </TooltipPrimitive.Provider>
  );
}

export function Tooltip({ label, children, side = "top", shortcut }: { label: ReactNode; children: ReactNode; side?: "top" | "bottom" | "left" | "right"; shortcut?: string }) {
  return (
    <TooltipPrimitive.Root>
      <TooltipPrimitive.Trigger asChild>{children}</TooltipPrimitive.Trigger>
      <TooltipPrimitive.Portal>
        <TooltipPrimitive.Content
          side={side}
          sideOffset={6}
          className="animate-fade-in z-50 flex items-center gap-2 rounded-md bg-fg px-2 py-1 text-xs font-medium text-bg"
        >
          {label}
          {shortcut && <span className="font-mono text-[0.7rem] opacity-60">{shortcut}</span>}
        </TooltipPrimitive.Content>
      </TooltipPrimitive.Portal>
    </TooltipPrimitive.Root>
  );
}

/* ------------------------------------------------------------- IconButton */
export const IconButton = forwardRef<
  HTMLButtonElement,
  ButtonHTMLAttributes<HTMLButtonElement> & { label: string; size?: "sm" | "md"; active?: boolean; tooltip?: boolean; shortcut?: string; side?: "top" | "bottom" | "left" | "right" }
>(({ label, size = "md", active, tooltip = true, shortcut, side, className, children, ...props }, ref) => {
  const btn = (
    <button
      ref={ref}
      type="button"
      aria-label={label}
      className={cn(
        "inline-flex shrink-0 items-center justify-center rounded-lg text-muted transition-colors duration-150 hover:bg-hover hover:text-fg disabled:pointer-events-none disabled:opacity-40",
        size === "sm" ? "size-7" : "size-8",
        active && "bg-active text-fg",
        className,
      )}
      {...props}
    >
      {children}
    </button>
  );
  return tooltip ? (
    <Tooltip label={label} shortcut={shortcut} side={side}>
      {btn}
    </Tooltip>
  ) : (
    btn
  );
});
IconButton.displayName = "IconButton";

/* ------------------------------------------------------------------ Inputs */
export const Input = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(({ className, ...props }, ref) => (
  <input
    ref={ref}
    className={cn(
      "h-9 w-full rounded-lg border border-line-strong bg-elevated px-3 text-sm text-fg placeholder:text-faint transition-colors focus:border-accent focus:outline-none focus-visible:outline-none focus:ring-2 focus:ring-accent-soft disabled:opacity-60",
      className,
    )}
    {...props}
  />
));
Input.displayName = "Input";

export const Textarea = forwardRef<HTMLTextAreaElement, TextareaHTMLAttributes<HTMLTextAreaElement>>(({ className, ...props }, ref) => (
  <textarea
    ref={ref}
    className={cn(
      "w-full rounded-lg border border-line-strong bg-elevated px-3 py-2 text-sm leading-relaxed text-fg placeholder:text-faint transition-colors focus:border-accent focus:outline-none focus-visible:outline-none focus:ring-2 focus:ring-accent-soft",
      className,
    )}
    {...props}
  />
));
Textarea.displayName = "Textarea";

export function Select({ className, children, ...props }: React.SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <select
      className={cn(
        "h-9 w-full appearance-none rounded-lg border border-line-strong bg-elevated bg-[length:16px] bg-[right_0.6rem_center] bg-no-repeat pr-8 pl-3 text-sm text-fg focus:border-accent focus:outline-none focus-visible:outline-none focus:ring-2 focus:ring-accent-soft",
        "bg-[url('data:image/svg+xml;utf8,<svg xmlns=%22http://www.w3.org/2000/svg%22 viewBox=%220 0 24 24%22 fill=%22none%22 stroke=%22%23888%22 stroke-width=%222%22><path d=%22m6 9 6 6 6-6%22/></svg>')]",
        className,
      )}
      {...props}
    >
      {children}
    </select>
  );
}

export function Field({ label, hint, error, children, htmlFor }: { label: ReactNode; hint?: ReactNode; error?: string | null; children: ReactNode; htmlFor?: string }) {
  return (
    <div className="space-y-1.5">
      <label htmlFor={htmlFor} className="block text-[0.8125rem] font-medium text-fg">
        {label}
      </label>
      {children}
      {error ? <p className="text-xs text-danger">{error}</p> : hint ? <p className="text-xs leading-relaxed text-muted">{hint}</p> : null}
    </div>
  );
}

export function Switch({ checked, onChange, label, description, disabled }: { checked: boolean; onChange: (v: boolean) => void; label: ReactNode; description?: ReactNode; disabled?: boolean }) {
  const id = useId();
  return (
    <div className="flex items-start justify-between gap-6 py-1">
      <div className="min-w-0">
        <label htmlFor={id} className="text-sm font-medium text-fg">
          {label}
        </label>
        {description && <p className="mt-0.5 text-[0.8125rem] leading-relaxed text-muted">{description}</p>}
      </div>
      <button
        id={id}
        role="switch"
        type="button"
        aria-checked={checked}
        disabled={disabled}
        onClick={() => onChange(!checked)}
        className={cn(
          "relative mt-0.5 inline-flex h-5 w-9 shrink-0 items-center rounded-full transition-colors duration-200 disabled:opacity-50",
          checked ? "bg-accent" : "bg-line-strong",
        )}
      >
        <span className={cn("inline-block size-4 rounded-full bg-white shadow-sm transition-transform duration-200", checked ? "translate-x-[18px]" : "translate-x-0.5")} />
      </button>
    </div>
  );
}

export function SegmentedControl<T extends string>({ value, onChange, options, label }: { value: T; onChange: (v: T) => void; options: { value: T; label: ReactNode }[]; label: string }) {
  return (
    <div role="radiogroup" aria-label={label} className="inline-flex rounded-lg bg-subtle p-0.5">
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          role="radio"
          aria-checked={value === o.value}
          onClick={() => onChange(o.value)}
          className={cn(
            "flex h-7 items-center gap-1.5 rounded-md px-3 text-[0.8125rem] font-medium transition-colors",
            value === o.value ? "bg-elevated text-fg shadow-sm" : "text-muted hover:text-fg",
          )}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

/* ------------------------------------------------------------------ Status */
const STATUS: Record<ProviderStatus, { label: string; className: string }> = {
  connected: { label: "Connected", className: "bg-success" },
  error: { label: "Unreachable", className: "bg-danger" },
  unknown: { label: "Not tested", className: "bg-faint" },
  disabled: { label: "Disabled", className: "bg-line-strong" },
};

export function StatusDot({ status, withLabel, className }: { status: ProviderStatus; withLabel?: boolean; className?: string }) {
  const s = STATUS[status] ?? STATUS.unknown;
  return (
    <span className={cn("inline-flex items-center gap-1.5", className)}>
      <span className={cn("size-1.5 shrink-0 rounded-full", s.className)} aria-hidden />
      {withLabel ? <span>{s.label}</span> : <span className="sr-only">{s.label}</span>}
    </span>
  );
}

export function Spinner({ className }: { className?: string }) {
  return <Loader2 className={cn("size-4 animate-spin text-muted", className)} aria-label="Loading" />;
}

export function Kbd({ children }: { children: ReactNode }) {
  return <kbd className="rounded border border-line bg-elevated px-1 font-mono text-[0.68rem] text-faint">{children}</kbd>;
}

/* ------------------------------------------------------------------ Dialog */
export function Dialog({
  open,
  onOpenChange,
  title,
  description,
  children,
  footer,
  size = "md",
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: ReactNode;
  description?: ReactNode;
  children?: ReactNode;
  footer?: ReactNode;
  size?: "sm" | "md" | "lg";
}) {
  return (
    <DialogPrimitive.Root open={open} onOpenChange={onOpenChange}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Overlay className="animate-fade-in fixed inset-0 z-40 bg-black/25 dark:bg-black/50" />
        <DialogPrimitive.Content
          className={cn(
            "animate-pop-in fixed top-[8vh] left-1/2 z-50 flex max-h-[84vh] w-[calc(100vw-2rem)] -translate-x-1/2 flex-col rounded-2xl border border-line bg-elevated shadow-pop focus:outline-none",
            size === "sm" && "max-w-md",
            size === "md" && "max-w-xl",
            size === "lg" && "max-w-3xl",
          )}
        >
          <div className="flex items-start justify-between gap-4 px-6 pt-5 pb-3">
            <div className="min-w-0">
              <DialogPrimitive.Title className="text-base font-semibold text-fg">{title}</DialogPrimitive.Title>
              {description ? (
                <DialogPrimitive.Description className="mt-1 text-sm leading-relaxed text-muted">{description}</DialogPrimitive.Description>
              ) : (
                <DialogPrimitive.Description className="sr-only">{typeof title === "string" ? title : "Dialog"}</DialogPrimitive.Description>
              )}
            </div>
            <DialogPrimitive.Close asChild>
              <IconButton label="Close" size="sm" tooltip={false} className="-mt-0.5 -mr-2">
                <X className="size-4" />
              </IconButton>
            </DialogPrimitive.Close>
          </div>
          {children && <div className="min-h-0 flex-1 overflow-y-auto px-6 pb-2">{children}</div>}
          {footer && <div className="flex items-center justify-end gap-2 px-6 pt-3 pb-5">{footer}</div>}
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  );
}

export function ConfirmDialog({
  open,
  onOpenChange,
  title,
  description,
  confirmLabel = "Delete",
  destructive = true,
  onConfirm,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  description?: ReactNode;
  confirmLabel?: string;
  destructive?: boolean;
  onConfirm: () => Promise<void> | void;
}) {
  const [busy, setBusy] = useState(false);
  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      title={title}
      description={description}
      size="sm"
      footer={
        <>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button
            variant={destructive ? "danger" : "primary"}
            loading={busy}
            autoFocus
            onClick={async () => {
              setBusy(true);
              try {
                await onConfirm();
                onOpenChange(false);
              } finally {
                setBusy(false);
              }
            }}
          >
            {confirmLabel}
          </Button>
        </>
      }
    />
  );
}

/* -------------------------------------------------------------------- Menu */
export const MenuRoot = Menu.Root;
export const MenuTrigger = Menu.Trigger;

export function MenuContent({ children, align = "start", side, className }: { children: ReactNode; align?: "start" | "end" | "center"; side?: "top" | "bottom" | "left" | "right"; className?: string }) {
  return (
    <Menu.Portal>
      <Menu.Content
        align={align}
        side={side}
        sideOffset={4}
        className={cn("animate-pop-in z-50 min-w-[11rem] rounded-xl border border-line bg-elevated p-1 shadow-pop focus:outline-none", className)}
      >
        {children}
      </Menu.Content>
    </Menu.Portal>
  );
}

export function MenuItem({ children, onSelect, icon, destructive, disabled }: { children: ReactNode; onSelect: () => void; icon?: ReactNode; destructive?: boolean; disabled?: boolean }) {
  return (
    <Menu.Item
      disabled={disabled}
      onSelect={onSelect}
      className={cn(
        "flex h-8 cursor-default items-center gap-2.5 rounded-lg px-2.5 text-[0.8125rem] outline-none select-none data-[disabled]:opacity-40 data-[highlighted]:bg-hover",
        destructive ? "text-danger" : "text-fg",
      )}
    >
      {icon && <span className="flex size-4 items-center justify-center text-muted [&>svg]:size-[15px]">{icon}</span>}
      {children}
    </Menu.Item>
  );
}

export function MenuSeparator() {
  return <Menu.Separator className="my-1 h-px bg-line" />;
}
