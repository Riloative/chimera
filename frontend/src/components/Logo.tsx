import { cn } from "@/lib/utils";

/** Chimera mark: three arcs (the three heads) around a single core. */
export function LogoMark({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 32 32" className={cn("size-6", className)} aria-hidden>
      <circle
        cx="16"
        cy="16"
        r="10"
        fill="none"
        stroke="currentColor"
        strokeWidth="3"
        strokeLinecap="round"
        strokeDasharray="16 4.94"
        transform="rotate(-62 16 16)"
      />
      <circle cx="16" cy="16" r="3" fill="currentColor" />
    </svg>
  );
}

export function Logo({ className }: { className?: string }) {
  return (
    <span className={cn("inline-flex items-center gap-2 text-fg", className)}>
      <LogoMark className="size-[22px] text-accent" />
      <span className="text-[0.95rem] font-semibold tracking-[-0.01em]">Chimera</span>
    </span>
  );
}
