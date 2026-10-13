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

  it("renders fenced code with language label, highlighting and copy button", () => {
    const { container } = render(<Markdown content={"```python\ndef f():\n    return 1\n```"} />);
    expect(screen.getByText("python")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Copy code" })).toBeInTheDocument();
    expect(container.querySelector("code.hljs, code .hljs-keyword")).not.toBeNull();
  });

  it("renders GFM tables, task lists and math", () => {
    const { container } = render(<Markdown content={"| a | b |\n|---|---|\n| 1 | 2 |\n\n- [x] done\n\n$E=mc^2$"} />);
    expect(container.querySelector("table")).not.toBeNull();
    expect(container.querySelector('input[type="checkbox"]')).not.toBeNull();
    expect(container.querySelector(".katex")).not.toBeNull();
  });

  it("normalizes \\( \\) and \\[ \\] math delimiters", () => {
    expect(normalizeMath("a \\(x\\) b \\[y\\]")).toBe("a $x$ b $$y$$");
  });
});
