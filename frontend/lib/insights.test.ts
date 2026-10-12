import { describe, expect, it } from "vitest";

import type { AspectStats } from "./api";
import {
  comparison,
  highlight,
  quoteText,
  spike,
  storePct,
  strength,
  timeAgo,
  verdict,
} from "./insights";

const aspect = (name: string, positive_pct: number | null, total = 100): AspectStats => ({
  aspect: name,
  positive: 0,
  negative: 0,
  total,
  positive_pct,
  quotes: [],
});
const sample = (positive_pct: number | null) => ({
  positive: 0,
  negative: 0,
  total: 1000,
  positive_pct,
});

// Cities: Skylines II's real numbers (story at 41 mentions still counts).
const CITIES = [
  aspect("performance", 49.2, 122),
  aspect("price", 46.9, 81),
  aspect("bugs", 30.6, 170),
  aspect("story", 43.9, 41),
  aspect("gameplay", 68.9, 244),
];

describe("verdict", () => {
  it("names the best and the worst judged aspect", () => {
    expect(verdict({ aspects: CITIES })).toBe(
      "Players are most positive about gameplay (69%), but bugs and stability is the sore point at 31%.",
    );
  });

  it("calls a narrow spread flat instead of inventing a weakness", () => {
    const hades = [aspect("price", 95.2), aspect("story", 95.4), aspect("gameplay", 94.0)];
    expect(verdict({ aspects: hades })).toBe(
      "Players feel much the same about every topic they bring up: around 95% positive.",
    );
  });

  it("ignores aspects with too few mentions", () => {
    const few = [aspect("bugs", 0, 3), aspect("story", 98.6, 139)];
    expect(verdict({ aspects: few })).toBe(
      "Only story and writing comes up often enough to judge: 99% positive.",
    );
    expect(verdict({ aspects: [aspect("bugs", 0, 3), aspect("story", null, 0)] })).toBe(
      "Too few reviews mention specific topics to point out strengths or weaknesses.",
    );
  });

  it("uses the split and trails wording for middling weak spots", () => {
    expect(verdict({ aspects: [aspect("gameplay", 90), aspect("bugs", 55)] })).toBe(
      "Players are most positive about gameplay (90%), but they are split on bugs and stability (55%).",
    );
    expect(verdict({ aspects: [aspect("gameplay", 90), aspect("price", 70)] })).toBe(
      "Players are most positive about gameplay (90%), but price and value trails at 70%.",
    );
  });
});

describe("comparison", () => {
  it("contrasts Steam's share with a clearly weaker aspect", () => {
    expect(comparison({ aspects: CITIES, steam_sample: sample(65.6) })).toEqual({
      steamPct: 66,
      aspect: "bugs",
      aspectPct: 31,
      weaker: true,
      text: "66% of reviewers recommend it on Steam, but only 31% of reviews about bugs and stability read as positive.",
    });
  });

  it("softens the wording for a narrow gap", () => {
    const result = comparison({ aspects: [aspect("price", 72)], steam_sample: sample(80) });
    expect(result?.weaker).toBe(true);
    expect(result?.text).toBe(
      "80% of reviewers recommend it on Steam, but reviews about price and value are less positive: 72%.",
    );
  });

  it("drops the 'even' when a topic keeps up with a low Steam share", () => {
    const result = comparison({ aspects: [aspect("bugs", 37)], steam_sample: sample(40) });
    expect(result?.text).toBe(
      "40% of reviewers recommend it on Steam; bugs and stability, the lowest-rated topic, reads 37% positive.",
    );
  });

  it("says so when even the weakest aspect keeps up", () => {
    const portal = [aspect("story", 98.6), aspect("gameplay", 98.4)];
    expect(comparison({ aspects: portal, steam_sample: sample(98.3) })?.text).toBe(
      "98% of reviewers recommend it on Steam, and even gameplay, the lowest-rated topic, reads 98% positive.",
    );
  });

  it("calls no high topic a weak spot, however far below Steam", () => {
    // Portal 2: price and value at 92% against 98% is not a complaint.
    const result = comparison({ aspects: [aspect("price", 92.1)], steam_sample: sample(98.3) });
    expect(result?.weaker).toBe(false);
    expect(result?.text).toMatch(/even price and value, the lowest-rated topic, reads 92% positive/);
  });

  it("is absent without a judged aspect or a Steam share", () => {
    expect(comparison({ aspects: [aspect("bugs", 0, 3)], steam_sample: sample(90) })).toBeNull();
    expect(comparison({ aspects: CITIES, steam_sample: sample(null) })).toBeNull();
  });
});

describe("storePct", () => {
  it("truncates like Steam's store page", () => {
    // Stored ratings checked against the live store pages on 2026-10-02.
    expect(storePct({ positive: 171921, total: 173897 })).toBe(98); // Portal 2, 98.86%
    expect(storePct({ positive: 20019, total: 34580 })).toBe(57); // Cities: Skylines II, 57.89%
    expect(storePct({ positive: 14370, total: 31932 })).toBe(45); // PAYDAY 3, 45.00%
    expect(storePct({ positive: 29, total: 100 })).toBe(29); // no float underflow below 29
  });
});

describe("spike", () => {
  const day = (date: string, negative: number) => ({ date, positive: 10, negative });
  const quiet = Array.from({ length: 9 }, (_, i) => day(`2026-09-0${i + 1}`, 6));

  it("calls out a day that dwarfs the typical one", () => {
    // Cities: Skylines II: 85 negative reviews on 2026-09-23 against a median of 6.
    expect(spike([...quiet, day("2026-09-23", 85)])?.text).toBe(
      "Sep 23, 2026 stands out: 85 negative reviews in one day, against 6 on a typical day.",
    );
  });

  it("stays quiet for ordinary variation and for small absolute numbers", () => {
    expect(spike([...quiet, day("2026-09-22", 19)])).toBeNull(); // PAYDAY 3's busiest day
    expect(spike([day("2026-09-01", 0), day("2026-09-02", 12)])).toBeNull();
    expect(spike([])).toBeNull();
  });
});

describe("quoteText", () => {
  it("marks a clause cut from mid-sentence", () => {
    expect(quoteText("and 60 FPS in heavy cities")).toBe("…and 60 FPS in heavy cities");
    expect(quoteText("Runs great.")).toBe("Runs great.");
  });
});

describe("strength", () => {
  it("names only the best topic, since the comparison names the worst", () => {
    expect(strength({ aspects: CITIES })).toBe("Players are most positive about gameplay (69%).");
  });

  it("keeps the flat sentence and has nothing to add for a single topic", () => {
    const hades = [aspect("price", 95.2), aspect("gameplay", 94.0)];
    expect(strength({ aspects: hades })).toBe(verdict({ aspects: hades }));
    expect(strength({ aspects: [aspect("gameplay", 80)] })).toBeNull();
  });
});

describe("highlight", () => {
  const text = "Runs fine. It crashes a lot. Price is fair.";

  it("cuts the text around the matched sentences, in order", () => {
    expect(
      highlight(text, [
        { aspect: "bugs", text: "It crashes a lot." },
        { aspect: "price", text: "Price is fair." },
      ]),
    ).toEqual([
      { text: "Runs fine. ", aspect: null },
      { text: "It crashes a lot.", aspect: "bugs" },
      { text: " ", aspect: null },
      { text: "Price is fair.", aspect: "price" },
    ]);
  });

  it("skips a match it can't find and one that overlaps", () => {
    expect(
      highlight(text, [
        { aspect: "bugs", text: "[b]It crashes[/b]" },
        { aspect: "bugs", text: "It crashes a lot." },
        { aspect: "price", text: "crashes" },
      ]),
    ).toEqual([
      { text: "Runs fine. ", aspect: null },
      { text: "It crashes a lot.", aspect: "bugs" },
      { text: " Price is fair.", aspect: null },
    ]);
    expect(highlight(text, [])).toEqual([{ text, aspect: null }]);
  });
});

describe("timeAgo", () => {
  const now = new Date(2026, 9, 2, 9, 0).getTime(); // local time, like timeAgo's days
  const at = (...args: [number, number, number, number?, number?]) => new Date(...args).toISOString();

  it("counts calendar days, not 24-hour spans", () => {
    expect(timeAgo(at(2026, 9, 2, 1), now)).toBe("today");
    expect(timeAgo(at(2026, 9, 1, 23), now)).toBe("yesterday");
    expect(timeAgo(at(2026, 8, 29), now)).toBe("3 days ago");
    expect(timeAgo(at(2026, 9, 3, 0, 30), now)).toBe("today"); // a client clock behind the server
  });

  it("moves to weeks, months and years", () => {
    expect(timeAgo(at(2026, 8, 18), now)).toBe("2 weeks ago");
    expect(timeAgo(at(2026, 6, 2), now)).toBe("3 months ago");
    expect(timeAgo(at(2024, 9, 2), now)).toBe("2 years ago");
  });

  it("marks a repeated sentence at its own place, not at its first occurrence", () => {
    expect(
      highlight("Great game. Great game.", [
        { aspect: "gameplay", text: "Great game." },
        { aspect: "story", text: "Great game." },
      ]),
    ).toEqual([
      { text: "Great game.", aspect: "gameplay" },
      { text: " ", aspect: null },
      { text: "Great game.", aspect: "story" },
    ]);
  });
});
