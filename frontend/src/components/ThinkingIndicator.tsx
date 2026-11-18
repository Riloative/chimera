import { cn } from "@/lib/utils";

/**
 * The one waiting indicator: a short arc orbiting a still core dot.
 *
 * Pure CSS: the arc is an HTML element rotated with a `transform` keyframe, so
 * the browser animates it on the compositor (no layout or paint per frame, no
 * JavaScript timers) and it stays smooth while tokens stream in. Sized in `em`
 * so it follows the surrounding text, coloured with `currentColor`.
 *
 * Always `aria-hidden`: callers keep their own localized status text (a live
 * region, a label) next to it. With reduced motion (system setting or the
 * app's own) the arc stops and the core breathes gently instead.
 */
export function ThinkingIndicator({ className, size = "md", core = true }: { className?: string; size?: "sm" | "md"; core?: boolean }) {
  return (
    <span aria-hidden className={cn("thinking-indicator", size === "sm" ? "text-[0.75em]" : "text-[1em]", className)} data-testid="thinking-indicator">
      <span className="thinking-orbit" />
      {core && <span className="thinking-core" />}
    </span>
  );
}
