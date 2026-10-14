import { readFileSync } from "node:fs";
import { join } from "node:path";
import { dedupeKey, displayDomain, findUrls, shortUrl } from "./urls";

interface Case {
  name: string;
  text: string;
  urls: string[];
  limit?: number;
  total?: number;
}

const cases: Case[] = JSON.parse(
  readFileSync(join(__dirname, "../../../backend/tests/fixtures/url_detection.json"), "utf-8"),
).cases;

describe("findUrls matches the server's detection", () => {
  for (const c of cases) {
    it(c.name, () => {
      const found = findUrls(c.text, c.limit ?? 5);
      expect(found.urls).toEqual(c.urls);
      if (c.total !== undefined) expect(found.total).toBe(c.total);
    });
  }
});

describe("url helpers", () => {
  it("normalizes for de-duplication", () => {
    expect(dedupeKey("HTTPS://Example.COM/")).toBe("https://example.com");
    expect(dedupeKey("https://example.com/A#x")).toBe("https://example.com/A");
  });
  it("formats for display", () => {
    expect(displayDomain("https://www.news.example.org/a")).toBe("news.example.org");
    expect(shortUrl("https://www.example.com/very/long/path/that/keeps/going/and/going/forever", 20)).toBe("example.com/very/lo…");
  });
});
