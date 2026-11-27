import { render, screen } from "@testing-library/react";
import { Markdown, normalizeMath } from "./Markdown";

describe("Markdown", () => {
  it("never renders raw HTML from model output", () => {
    const { container } = render(<Markdown content={'Hello <img src=x onerror="alert(1)"> <script>alert(2)</script>'} />);
    expect(container.querySelector("script")).toBeNull();
    expect(container.querySelector("img")).toBeNull();
    expect(container.textContent).toContain("<script>");
  });

  it("strips javascript: URLs and opens links safely", () => {
    const { container } = render(<Markdown content={"[bad](javascript:alert(1)) [good](https://example.com)"} />);
    const links = container.querySelectorAll("a");
    expect(links[0].getAttribute("href") ?? "").not.toContain("javascript");
    expect(links[1].getAttribute("href")).toBe("https://example.com");
    expect(links[1].getAttribute("rel")).toContain("noopener");
  });

  it("blocks remote images (no third-party requests)", () => {
    const { container } = render(<Markdown content={"![tracker](https://evil.example/p.png)"} />);
    expect(container.querySelector("img")).toBeNull();
  });

  it("renders fenced code with language label, highlighting and copy button", async () => {
    const { container } = render(<Markdown content={"```python\ndef f():\n    return 1\n```"} />);
    expect(screen.getByText("python")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Copy code" })).toBeInTheDocument();
    // highlight.js loads on first use, then the code is coloured.
    await vi.waitFor(() => expect(container.querySelector("code.hljs, code .hljs-keyword")).not.toBeNull());
  });

  it("renders GFM tables, task lists and math", async () => {
    const { container } = render(<Markdown content={"| a | b |\n|---|---|\n| 1 | 2 |\n\n- [x] done\n\n$E=mc^2$"} />);
    expect(container.querySelector("table")).not.toBeNull();
    expect(container.querySelector('input[type="checkbox"]')).not.toBeNull();
    await vi.waitFor(() => expect(container.querySelector(".katex")).not.toBeNull()); // KaTeX loads on first use
  });

  it("loads neither KaTeX nor highlight.js for plain answers", async () => {
    const { container } = render(<Markdown content={"Just **text**, a list:\n\n- one\n- `inline code`"} />);
    await new Promise((r) => setTimeout(r, 50));
    expect(container.querySelector(".katex, .hljs")).toBeNull();
    expect(container.querySelector("strong")).toHaveTextContent("text");
  });

  it("normalizes \\( \\) and \\[ \\] math delimiters", () => {
    expect(normalizeMath("a \\(x\\) b \\[y\\]")).toBe("a $x$ b $$y$$");
  });
});
