import { groupConversations } from "./time";
import type { Conversation } from "./types";

const conv = (id: string, updated: Date, pinned = false): Conversation => ({
  id, title: id, title_source: "user", provider_id: null, model: null, system_prompt: null, params: {},
  collection_ids: [], pinned, created_at: updated.toISOString(), updated_at: updated.toISOString(),
});

describe("groupConversations", () => {
  it("groups by recency with pinned first", () => {
    const now = new Date(2026, 9, 13, 15, 0);
    const day = 86_400_000;
    const groups = groupConversations(
      [
        conv("today", new Date(now.getTime() - 3600_000)),
        conv("yesterday", new Date(now.getTime() - day)),
        conv("week", new Date(now.getTime() - 4 * day)),
        conv("old", new Date(now.getTime() - 90 * day)),
        conv("pinned-old", new Date(now.getTime() - 90 * day), true),
      ],
      now,
    );
    expect(groups.map((g) => [g.key, g.items.map((i) => i.id)])).toEqual([
      ["pinned", ["pinned-old"]],
      ["today", ["today"]],
      ["yesterday", ["yesterday"]],
      ["week", ["week"]],
      ["older", ["old"]],
    ]);
  });
});
