/** Small, consistent UI primitives built on Radix for accessibility. */
import * as DialogPrimitive from "@radix-ui/react-dialog";
import * as Menu from "@radix-ui/react-dropdown-menu";
import * as TooltipPrimitive from "@radix-ui/react-tooltip";
import { X } from "lucide-react";
import { useTranslation } from "react-i18next";
import { forwardRef, useId, useState, type ButtonHTMLAttributes, type ComponentProps, type InputHTMLAttributes, type ReactNode, type TextareaHTMLAttributes } from "react";
import { cn } from "@/lib/utils";
import { ThinkingIndicator } from "./ThinkingIndicator";
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
      size === "sm" ? "h-8 px-2.5 text-ui" : "h-9 px-3.5 text-sm",
      variant === "primary" && "bg-fg text-bg hover:opacity-90",
      variant === "secondary" && "bg-subtle text-fg hover:bg-active",
      variant === "outline" && "border border-line-strong text-fg hover:bg-hover",
      variant === "ghost" && "text-muted hover:bg-hover hover:text-fg",
      variant === "danger" && "bg-danger text-white hover:opacity-90",
      className,
    )}
    {...props}
  >
    {loading ? <ThinkingIndicator className="text-base" /> : icon}
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
          {shortcut && <span className="font-mono text-caption opacity-60">{shortcut}</span>}
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
      <label htmlFor={htmlFor} className="block text-ui font-medium text-fg">
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
        {description && <p className="mt-0.5 text-ui leading-relaxed text-muted">{description}</p>}
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
            "flex h-7 items-center gap-1.5 rounded-md px-3 text-ui font-medium transition-colors",
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
const STATUS_CLASS: Record<ProviderStatus, string> = {
  connected: "bg-success",
  error: "bg-danger",
  unknown: "bg-faint",
  disabled: "bg-line-strong",
};

export function StatusDot({ status, withLabel, className }: { status: ProviderStatus; withLabel?: boolean; className?: string }) {
  const { t } = useTranslation();
  const known = status in STATUS_CLASS ? status : "unknown";
  const label = t(`status.${known}`);
  return (
    <span className={cn("inline-flex items-center gap-1.5", className)}>
      <span className={cn("size-1.5 shrink-0 rounded-full", STATUS_CLASS[known])} aria-hidden />
      {withLabel ? <span>{label}</span> : <span className="sr-only">{label}</span>}
    </span>
  );
}

export function Spinner({ className }: { className?: string }) {
  const { t } = useTranslation();
  return (
    <span role="status" className={cn("inline-flex items-center text-base", className)}>
      <ThinkingIndicator />
      <span className="sr-only">{t("loading")}</span>
    </span>
  );
}

export function Kbd({ children }: { children: ReactNode }) {
  return <kbd className="rounded border border-line bg-elevated px-1 font-mono text-caption text-faint">{children}</kbd>;
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
  const { t } = useTranslation();
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
                <DialogPrimitive.Description className="sr-only">{typeof title === "string" ? title : t("dialog")}</DialogPrimitive.Description>
              )}
            </div>
            <DialogPrimitive.Close asChild>
              <IconButton label={t("close")} size="sm" tooltip={false} className="-mt-0.5 -mr-2">
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
  confirmLabel,
  destructive = true,
  onConfirm,
  children,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  description?: ReactNode;
  confirmLabel?: string;
  destructive?: boolean;
  onConfirm: () => Promise<void> | void;
  children?: ReactNode;
}) {
  const { t } = useTranslation();
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
            {t("cancel")}
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
            {confirmLabel ?? t("delete")}
          </Button>
        </>
      }
    >
      {children}
    </Dialog>
  );
}

/** Loading → spinner; failed → what went wrong and "Try again" (never a spinner forever). */
export function LoadState({ error, onRetry, className }: { error: string | null; onRetry: () => void; className?: string }) {
  const { t } = useTranslation();
  if (!error) return <Spinner />;
  return (
    <div role="alert" className={cn("flex flex-wrap items-center gap-x-3 gap-y-2 rounded-xl border border-danger/25 bg-danger-soft px-4 py-3 text-ui", className)}>
      <p className="min-w-0 flex-1 text-fg">
        {t("loadError")} <span className="text-muted">{error}</span>
      </p>
      <Button size="sm" variant="outline" onClick={onRetry}>
        {t("tryAgain")}
      </Button>
    </div>
  );
}

/* -------------------------------------------------------------------- Menu */
/** Non-modal: a modal menu hides the rest of the page from assistive technology while
 * its controls stay focusable (axe: aria-hidden-focus). Focus handling is unchanged. */
export function MenuRoot(props: ComponentProps<typeof Menu.Root>) {
  return <Menu.Root modal={false} {...props} />;
}
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
        "flex h-8 cursor-default items-center gap-2.5 rounded-lg px-2.5 text-ui outline-none select-none data-[disabled]:opacity-40 data-[highlighted]:bg-hover",
        destructive ? "text-danger" : "text-fg",
      )}
    >
      {icon && <span className="flex size-4 items-center justify-center text-muted [&>svg]:size-[15px]">{icon}</span>}
      {children}
    </Menu.Item>
  );
}

/** A titled group of choices inside a menu (e.g. "In this conversation"). */
export function MenuLabel({ children }: { children: ReactNode }) {
  return <Menu.Label className="px-2.5 pt-1.5 pb-1 text-caption font-medium text-faint">{children}</Menu.Label>;
}

export const MenuRadioGroup = Menu.RadioGroup;

export function MenuRadioItem({ value, children, hint, testId }: { value: string; children: ReactNode; hint?: ReactNode; testId?: string }) {
  return (
    <Menu.RadioItem
      value={value}
      data-testid={testId}
      className="flex min-h-8 cursor-default items-start gap-2.5 rounded-lg py-1.5 pr-2.5 pl-2.5 text-ui text-fg outline-none select-none data-[highlighted]:bg-hover"
    >
      <span className="mt-[3px] flex size-4 shrink-0 items-center justify-center" aria-hidden>
        <span className="size-2 rounded-full border border-line-strong in-data-[state=checked]:border-fg in-data-[state=checked]:bg-fg" />
      </span>
      <span className="min-w-0">
        <span className="block">{children}</span>
        {hint && <span className="block text-caption leading-snug text-muted">{hint}</span>}
      </span>
    </Menu.RadioItem>
  );
}

export function MenuSeparator() {
  return <Menu.Separator className="my-1 h-px bg-line" />;
}
