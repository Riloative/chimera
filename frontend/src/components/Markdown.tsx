import { Check, Copy, WrapText } from "lucide-react";
import { memo, useState, type ComponentProps, type ReactNode } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import rehypeHighlight from "rehype-highlight";
import rehypeKatex from "rehype-katex";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import { cn, copyText } from "@/lib/utils";

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
  const [copied, setCopied] = useState(false);
  const [wrap, setWrap] = useState(false);
  return (
    <div className="group/code relative mb-4 overflow-hidden rounded-xl border border-[var(--code-border)] bg-code">
      <div className="flex h-9 items-center justify-between border-b border-[var(--code-border)] pr-1.5 pl-3.5">
        <span className="font-mono text-[0.72rem] text-faint">{language || "text"}</span>
        <div className="flex items-center gap-0.5">
          <button
            type="button"
            onClick={() => setWrap((w) => !w)}
            aria-pressed={wrap}
            aria-label="Toggle line wrap"
            title="Toggle line wrap"
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
            aria-label={copied ? "Copied" : "Copy code"}
            className="flex h-7 items-center gap-1.5 rounded-md px-2 text-[0.72rem] text-faint transition-colors hover:bg-hover hover:text-fg"
          >
            {copied ? <Check className="size-3.5" /> : <Copy className="size-3.5" />}
            {copied ? "Copied" : "Copy"}
          </button>
        </div>
      </div>
      <pre className={cn("overflow-x-auto px-4 py-3.5 font-mono text-[0.8125rem] leading-[1.65]", wrap && "break-words whitespace-pre-wrap")}>
        {children}
      </pre>
    </div>
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
  a: ({ href, children, ...props }) => (
    <a href={href} target="_blank" rel="noopener noreferrer nofollow" {...props}>
      {children}
    </a>
  ),
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
      <span className="text-muted">[image: {alt || "external image blocked"}]</span>
    ),
};

const remarkPlugins: ComponentProps<typeof ReactMarkdown>["remarkPlugins"] = [remarkGfm, [remarkMath, { singleDollarTextMath: true }]];
const rehypePlugins: ComponentProps<typeof ReactMarkdown>["rehypePlugins"] = [
  [rehypeKatex, { throwOnError: false, strict: "ignore", trust: false }],
  [rehypeHighlight, { detect: false, ignoreMissing: true }],
];

/** Normalize \( \) and \[ \] math delimiters that many local models emit. */
export function normalizeMath(text: string): string {
  return text
    .replace(/\\\[([\s\S]+?)\\\]/g, (_, m) => `$$${m}$$`)
    .replace(/\\\(([\s\S]+?)\\\)/g, (_, m) => `$${m}$`);
}

export const Markdown = memo(function Markdown({ content, className, streaming }: { content: string; className?: string; streaming?: boolean }) {
  return (
    <div className={cn("prose-chimera", streaming && "is-streaming", className)}>
      <ReactMarkdown remarkPlugins={remarkPlugins} rehypePlugins={rehypePlugins} components={components}>
        {normalizeMath(content)}
      </ReactMarkdown>
    </div>
  );
});
