import { Check, Copy, ExternalLink, ImageOff, WrapText } from "lucide-react";
import { createContext, memo, useContext, useEffect, useMemo, useState, type ComponentProps, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import { cn, copyText } from "@/lib/utils";
import { Tooltip } from "./ui";

/**
 * Safe Markdown rendering: raw HTML in model output is NOT interpreted
 * (no rehype-raw), and react-markdown's default URL transform strips
 * javascript:/data: links. Everything renders as inert React elements.
 */

function textOf(node: ReactNode): string {
  if (node == null || typeof node === "boolean") return "";
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(textOf).join("");
  if (typeof node === "object" && "props" in node) return textOf((node as { props: { children?: ReactNode } }).props.children);
  return "";
}

export function CodeBlock({ language, code, children }: { language: string; code: string; children: ReactNode }) {
  const { t } = useTranslation("chat");
  const [copied, setCopied] = useState(false);
  const [wrap, setWrap] = useState(false);
  return (
    <div className="group/code relative mb-4 overflow-hidden rounded-xl border border-[var(--code-border)] bg-code">
      <div className="flex h-9 items-center justify-between border-b border-[var(--code-border)] pr-1.5 pl-3.5">
        <span className="font-mono text-caption text-faint">{language || t("code.plainText")}</span>
        <div className="flex items-center gap-0.5">
          <button
            type="button"
            onClick={() => setWrap((w) => !w)}
            aria-pressed={wrap}
            aria-label={t("code.wrap")}
            title={t("code.wrap")}
            className={cn("flex size-7 items-center justify-center rounded-md text-faint transition-colors hover:bg-hover hover:text-fg", wrap && "text-fg")}
          >
            <WrapText className="size-3.5" />
          </button>
          <button
            type="button"
            onClick={async () => {
              if (await copyText(code)) {
                setCopied(true);
                setTimeout(() => setCopied(false), 1600);
              }
            }}
            aria-label={copied ? t("code.copied") : t("code.copy")}
            className="flex h-7 items-center gap-1.5 rounded-md px-2 text-caption text-faint transition-colors hover:bg-hover hover:text-fg"
          >
            {copied ? <Check className="size-3.5" /> : <Copy className="size-3.5" />}
            {copied ? t("code.copiedShort") : t("code.copyShort")}
          </button>
        </div>
      </div>
      <pre className={cn("overflow-x-auto px-4 py-3.5 font-mono text-ui leading-[1.65]", wrap && "break-words whitespace-pre-wrap")}>
        {children}
      </pre>
    </div>
  );
}

/**
 * Remote images are never loaded automatically: they would reveal the user's
 * IP address and could carry data out in their URL (a classic way for a
 * prompt-injected answer to exfiltrate a conversation). The user can open one
 * explicitly in a new tab.
 */
function BlockedImage({ alt, src }: { alt?: string; src?: string }) {
  const { t } = useTranslation(["web", "chat"]);
  const remote = typeof src === "string" && /^https?:\/\//i.test(src);
  return (
    <span className="inline-flex max-w-full items-center gap-1.5 rounded-md border border-dashed border-line px-1.5 py-0.5 align-middle text-ui text-muted">
      <ImageOff className="size-3.5 shrink-0" aria-hidden />
      <span className="truncate">{alt ? t("web:image.blocked", { alt }) : t("web:image.blockedNoAlt")}</span>
      {remote && (
        <a href={src} target="_blank" rel="noopener noreferrer nofollow" className="inline-flex shrink-0 items-center gap-0.5 text-accent-text">
          {t("web:image.open")} <ExternalLink className="size-3" aria-hidden />
        </a>
      )}
    </span>
  );
}

/* ------------------------------------------------------------- citations */
export interface CitationTarget {
  title: string;
  url: string;
  domain: string;
}
type Citations = Record<number, CitationTarget>;
const CitationContext = createContext<Citations | null>(null);
const CITE_PREFIX = "#cite-";
const CITE_RE = /\[(\d{1,3})\](?!\()/g;

interface MdNode {
  type: string;
  value?: string;
  url?: string;
  children?: MdNode[];
}

/** remark plugin: turn "[n]" in text (not code, not links) into citation links,
 * but only for numbers that are real sources; anything else stays plain text. */
function remarkCitations(options: { valid: Set<number> }) {
  return (tree: MdNode) => {
    const walk = (node: MdNode) => {
      if (!node.children || node.type === "link" || node.type === "linkReference") return;
      const next: MdNode[] = [];
      for (const child of node.children) {
        if (child.type !== "text" || !child.value?.includes("[")) {
          walk(child);
          next.push(child);
          continue;
        }
        const value = child.value;
        let last = 0;
        for (const m of value.matchAll(CITE_RE)) {
          const n = Number(m[1]);
          if (!options.valid.has(n)) continue;
          if (m.index! > last) next.push({ type: "text", value: value.slice(last, m.index) });
          next.push({ type: "link", url: `${CITE_PREFIX}${n}`, children: [{ type: "text", value: String(n) }] });
          last = m.index! + m[0].length;
        }
        if (last < value.length) next.push({ type: "text", value: value.slice(last) });
      }
      node.children = next;
    };
    walk(tree);
  };
}

function Citation({ n, target }: { n: number; target: CitationTarget }) {
  const { t } = useTranslation("web");
  return (
    <Tooltip
      label={
        <span className="block max-w-72">
          <span className="block truncate font-medium">{target.title}</span>
          <span className="block truncate opacity-75">{target.url}</span>
        </span>
      }
    >
      <a
        href={target.url}
        target="_blank"
        rel="noopener noreferrer nofollow"
        aria-label={t("sources.cite", { n, title: target.title })}
        data-citation={n}
        className="citation mx-px inline-flex h-[1.15rem] min-w-[1.15rem] -translate-y-px items-center justify-center rounded-md bg-subtle px-1 align-middle font-mono text-caption font-medium text-muted no-underline transition-colors hover:bg-accent-soft hover:text-accent-text focus-visible:bg-accent-soft focus-visible:text-accent-text"
      >
        {n}
      </a>
    </Tooltip>
  );
}

function Anchor({ href, children, ...props }: ComponentProps<"a">) {
  const citations = useContext(CitationContext);
  if (href?.startsWith(CITE_PREFIX) && citations) {
    const n = Number(href.slice(CITE_PREFIX.length));
    const target = citations[n];
    if (target) return <Citation n={n} target={target} />;
  }
  return (
    <a href={href} target="_blank" rel="noopener noreferrer nofollow" {...props}>
      {children}
    </a>
  );
}

const components: Components = {
  pre: ({ children }) => {
    const child = Array.isArray(children) ? children[0] : children;
    const props = (child as { props?: { className?: string; children?: ReactNode } })?.props ?? {};
    const language = /language-([\w+#-]+)/.exec(props.className || "")?.[1] ?? "";
    const code = textOf(props.children).replace(/\n$/, "");
    return (
      <CodeBlock language={language} code={code}>
        {children}
      </CodeBlock>
    );
  },
  code: ({ className, children, ...props }) => (
    <code className={cn(className, "font-mono")} {...props}>
      {children}
    </code>
  ),
  a: ({ node: _node, ...props }) => <Anchor {...props} />,
  table: ({ children, ...props }) => (
    <div className="table-wrap">
      <table {...props}>{children}</table>
    </div>
  ),
  img: ({ src, alt }) =>
    // Remote images would leak data to third parties; render only local/data images.
    typeof src === "string" && (src.startsWith("data:image/") || src.startsWith("/")) ? (
      <img src={src} alt={alt ?? ""} loading="lazy" />
    ) : (
      <BlockedImage alt={alt} src={typeof src === "string" ? src : undefined} />
    ),
};

const remarkPlugins: ComponentProps<typeof ReactMarkdown>["remarkPlugins"] = [remarkGfm, [remarkMath, { singleDollarTextMath: true }]];
type Plugins = NonNullable<ComponentProps<typeof ReactMarkdown>["rehypePlugins"]>;
type Pluggable = Plugins[number];

/*
 * KaTeX and highlight.js are more than half of the Markdown code, and most
 * answers need neither: each loads the first time an answer contains maths or
 * code. Until then maths shows as written and code without colours (both in
 * their final boxes, so little moves when they arrive).
 */
const lazyPlugin = (load: () => Promise<Pluggable>) => {
  let plugin: Pluggable | null = null;
  let pending: Promise<Pluggable> | null = null;
  return {
    get: () => plugin,
    load: () => (pending ??= load().then((p) => (plugin = p))),
  };
};
const katexPlugin = lazyPlugin(async () => {
  const [{ default: rehypeKatex }] = await Promise.all([import("rehype-katex"), import("katex/dist/katex.min.css")]);
  return [rehypeKatex, { throwOnError: false, strict: "ignore", trust: false }] as Pluggable;
});
const highlightPlugin = lazyPlugin(async () => {
  const { default: rehypeHighlight } = await import("rehype-highlight");
  return [rehypeHighlight, { detect: false, ignoreMissing: true }] as Pluggable;
});

const MATH_RE = /\$|\\\(|\\\[/;
const CODE_RE = /```|~~~/; // only fenced code carries a language to highlight

/** The rehype plugins this text needs; re-renders once a missing one has loaded. */
function useRehypePlugins(text: string): Plugins {
  const needsMath = MATH_RE.test(text);
  const needsCode = CODE_RE.test(text);
  const [, setLoaded] = useState(0);
  const missing = (needsMath && !katexPlugin.get()) || (needsCode && !highlightPlugin.get());
  useEffect(() => {
    if (!missing) return;
    let alive = true;
    const waits = [needsMath && katexPlugin.load(), needsCode && highlightPlugin.load()].filter(Boolean) as Promise<unknown>[];
    Promise.all(waits)
      .then(() => alive && setLoaded((n) => n + 1))
      .catch(() => undefined); // offline mid-session: keep the plain rendering
    return () => {
      alive = false;
    };
  }, [missing, needsMath, needsCode]);
  const math = needsMath ? katexPlugin.get() : null;
  const code = needsCode ? highlightPlugin.get() : null;
  return useMemo(() => [math, code].filter((p): p is Pluggable => !!p), [math, code]);
}

/** Normalize \( \) and \[ \] math delimiters that many local models emit. */
export function normalizeMath(text: string): string {
  return text
    .replace(/\\\[([\s\S]+?)\\\]/g, (_, m) => `$$${m}$$`)
    .replace(/\\\(([\s\S]+?)\\\)/g, (_, m) => `$${m}$`);
}

export const Markdown = memo(function Markdown({
  content,
  className,
  streaming,
  citations,
}: {
  content: string;
  className?: string;
  streaming?: boolean;
  /** Source number → target; "[n]" for other numbers stays plain text. */
  citations?: Citations;
}) {
  const plugins = useMemo(() => {
    const valid = new Set(Object.keys(citations ?? {}).map(Number));
    return valid.size ? [...(remarkPlugins ?? []), [remarkCitations, { valid }] as never] : remarkPlugins;
  }, [citations]);
  const text = normalizeMath(content);
  const rehypePlugins = useRehypePlugins(text);
  return (
    <div className={cn("prose-chimera", streaming && "is-streaming", className)}>
      <CitationContext.Provider value={citations ?? null}>
        <ReactMarkdown remarkPlugins={plugins} rehypePlugins={rehypePlugins} components={components}>
          {text}
        </ReactMarkdown>
      </CitationContext.Provider>
    </div>
  );
});
