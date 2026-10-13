import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import i18n from "@/i18n";
import { ApiError } from "@/lib/api";
import { errorText, providerErrorText } from "@/lib/errors";
import { formatTime, groupConversations, relativeTime } from "@/lib/time";
import { formatBytes } from "@/lib/utils";
import { LANGUAGES, resources } from "./resources";

type Tree = { [k: string]: string | Tree };
const PLURAL_SUFFIX = /_(zero|one|two|few|many|other)$/;
const REQUIRED_PLURALS: Record<string, string[]> = {
  en: ["one", "other"],
  lt: ["one", "few", "many", "other"],
};

function flatten(tree: Tree, prefix = ""): Record<string, string> {
  const out: Record<string, string> = {};
  for (const [k, v] of Object.entries(tree)) {
    const key = prefix ? `${prefix}.${k}` : k;
    if (typeof v === "string") out[key] = v;
    else Object.assign(out, flatten(v, key));
  }
  return out;
}

function placeholders(s: string): string[] {
  return [...s.matchAll(/{{\s*([\w.]+)\s*}}/g)].map((m) => m[1]).sort();
}

function tags(s: string): string[] {
  return [...s.matchAll(/<\/?([a-zA-Z]+)>/g)].map((m) => m[0]).sort();
}

describe("locale catalogs", () => {
  const namespaces = Object.keys(resources.en) as (keyof typeof resources.en)[];

  it("every language has the same namespaces", () => {
    for (const lng of LANGUAGES) expect(Object.keys(resources[lng]).sort()).toEqual([...namespaces].sort());
  });

  for (const ns of Object.keys(resources.en) as (keyof typeof resources.en)[]) {
    it(`"${ns}" has matching keys, plural forms, placeholders and tags`, () => {
      const flat = Object.fromEntries(LANGUAGES.map((l) => [l, flatten(resources[l][ns] as unknown as Tree)])) as Record<string, Record<string, string>>;
      const base = (keys: string[]) => new Set(keys.map((k) => k.replace(PLURAL_SUFFIX, "")));
      const enBase = base(Object.keys(flat.en));
      for (const lng of LANGUAGES) {
        expect([...base(Object.keys(flat[lng]))].sort(), `${lng}:${ns} keys`).toEqual([...enBase].sort());
        // plural keys must have every category the language needs
        const pluralBases = new Set(Object.keys(flat[lng]).filter((k) => PLURAL_SUFFIX.test(k)).map((k) => k.replace(PLURAL_SUFFIX, "")));
        const enPlural = new Set(Object.keys(flat.en).filter((k) => PLURAL_SUFFIX.test(k)).map((k) => k.replace(PLURAL_SUFFIX, "")));
        expect([...pluralBases].sort(), `${lng}:${ns} plural keys`).toEqual([...enPlural].sort());
        for (const b of pluralBases) {
          for (const cat of REQUIRED_PLURALS[lng]) expect(flat[lng][`${b}_${cat}`], `${lng}:${ns}:${b}_${cat}`).toBeTypeOf("string");
        }
      }
      // placeholders and markup must match the English source
      for (const [key, en] of Object.entries(flat.en)) {
        const b = key.replace(PLURAL_SUFFIX, "");
        for (const lng of LANGUAGES) {
          const variants = Object.entries(flat[lng]).filter(([k]) => k.replace(PLURAL_SUFFIX, "") === b);
          for (const [k, v] of variants) {
            expect(placeholders(v), `${lng}:${ns}:${k} placeholders`).toEqual(placeholders(en));
            expect(tags(v), `${lng}:${ns}:${k} tags`).toEqual(tags(en));
            expect(v.trim().length, `${lng}:${ns}:${k} empty`).toBeGreaterThan(0);
          }
        }
      }
    });
  }

  it("Lithuanian text uses its own letters (not an untranslated copy)", () => {
    const lt = Object.values(resources.lt).flatMap((ns) => Object.values(flatten(ns as unknown as Tree))).join(" ");
    expect(lt).toMatch(/[ąčęėįšųūž]/);
    const en = flatten(resources.en.auth as unknown as Tree);
    const ltAuth = flatten(resources.lt.auth as unknown as Tree);
    const identical = Object.keys(en).filter((k) => en[k] === ltAuth[k]);
    expect(identical).toEqual([]);
  });
});

describe("Lithuanian plurals", () => {
  afterAll(() => i18n.changeLanguage("en"));
  it.each([
    [1, "1 pokalbis"],
    [2, "2 pokalbiai"],
    [10, "10 pokalbių"],
    [11, "11 pokalbių"],
    [21, "21 pokalbis"],
    [22, "22 pokalbiai"],
    [0, "0 pokalbių"],
  ])("%i → %s", async (count, expected) => {
    await i18n.changeLanguage("lt");
    expect(i18n.t("settings:data.conversationCount", { count })).toBe(expected);
  });

  it("English uses one/other", async () => {
    await i18n.changeLanguage("en");
    expect(i18n.t("settings:data.conversationCount", { count: 1 })).toBe("1 conversation");
    expect(i18n.t("settings:data.conversationCount", { count: 21 })).toBe("21 conversations");
  });
});

describe("Intl formatting follows the language", () => {
  const iso = "2026-03-05T14:07:00";
  const now = new Date("2026-10-13T12:00:00");
  it("dates and times", () => {
    expect(formatTime(iso, "en", now)).toMatch(/^March 5, /);
    expect(formatTime(iso, "lt", now)).toBe("kovo 5 d., 14:07");
    expect(formatTime(iso, "en", now)).toMatch(/2:07\sPM/);
  });
  it("relative times", () => {
    const yesterday = new Date(now.getTime() - 86_400_000).toISOString();
    expect(relativeTime(yesterday, "en", now.getTime())).toBe("yesterday");
    expect(relativeTime(yesterday, "lt", now.getTime())).toBe("vakar");
    const fiveMin = new Date(now.getTime() - 5 * 60_000).toISOString();
    expect(relativeTime(fiveMin, "lt", now.getTime())).toMatch(/prieš 5 min/);
  });
  it("numbers and file sizes", () => {
    expect(formatBytes(1536, "en")).toBe("1.5 kB");
    expect(formatBytes(1536, "lt")).toBe("1,5 kB");
  });
  it("sidebar groups are computed independently of language", () => {
    const groups = groupConversations([], now);
    expect(groups).toEqual([]);
  });
});

describe("error codes become localized messages", () => {
  afterAll(() => i18n.changeLanguage("en"));
  it("maps API error codes with parameters in both languages", async () => {
    const err = new ApiError("Too many attempts.", 429, "rate_limited", { retry_after: 30 });
    await i18n.changeLanguage("en");
    expect(errorText(err)).toBe("Too many attempts. Try again in 30 s.");
    await i18n.changeLanguage("lt");
    expect(errorText(err)).toBe("Per daug bandymų. Bandykite dar kartą po 30 s.");
    expect(errorText(new ApiError("x", 401, "invalid_credentials"))).toBe("Neteisingas prisijungimo vardas arba slaptažodis.");
  });
  it("maps provider codes and falls back to the server text for unknown codes", async () => {
    await i18n.changeLanguage("lt");
    expect(providerErrorText("connection", { name: "llama-server" }, "x")).toContain("Nepavyko prisijungti prie „llama-server“");
    expect(providerErrorText("some_new_code", {}, "Raw server text")).toBe("Raw server text");
    expect(errorText(new ApiError("Fallback message", 400, "brand_new_code"))).toBe("Fallback message");
  });
});

describe("no hardcoded user-facing strings in components (best effort)", () => {
  const root = join(__dirname, "..");
  const files: string[] = [];
  const walk = (dir: string) => {
    for (const entry of readdirSync(dir, { withFileTypes: true })) {
      const p = join(dir, entry.name);
      if (entry.isDirectory()) walk(p);
      else if (p.endsWith(".tsx") && !p.endsWith(".test.tsx")) files.push(p);
    }
  };
  walk(root);
  // Brand and technical identifiers that are deliberately not translated.
  const ALLOWED = /^(Chimera|llama-server|Authorization|Enter|Esc|esc|OpenAI|Ollama|nomic-embed-text|English|Lietuvių|text|•+|…|→|↑|↓|↵|\/v1|⌘|Ctrl)$/;

  it("JSX text and labels go through t()", () => {
    const offenders: string[] = [];
    for (const file of files) {
      const src = readFileSync(file, "utf8");
      const lines = src.split("\n");
      lines.forEach((line, i) => {
        if (line.trim().startsWith("//") || line.includes("i18n-ignore")) return;
        // >Some words<  (text nodes between tags)
        for (const m of line.matchAll(/(?<![=-])>\s*([A-Za-z][A-Za-z ,.'!?’:;-]{2,})\s*</g)) {
          if (!ALLOWED.test(m[1].trim())) offenders.push(`${file}:${i + 1}: ${m[1].trim()}`);
        }
        // a line that is only plain words inside JSX
        const bare = line.trim();
        if (/^[A-Z][a-z]+( [a-z]+)+[.?!]?$/.test(bare) && !ALLOWED.test(bare)) offenders.push(`${file}:${i + 1}: ${bare}`);
        // literal attribute values that users see
        for (const m of line.matchAll(/\b(aria-label|title|placeholder|label|description|alt)="([^"]*[A-Za-z]{3,}[^"]*)"/g)) {
          if (!ALLOWED.test(m[2])) offenders.push(`${file}:${i + 1}: ${m[1]}="${m[2]}"`);
        }
      });
    }
    expect(offenders).toEqual([]);
  });
});
