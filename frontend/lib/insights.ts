// Plain-language reading of an /aspects answer: labels for the aspects and the templated
// sentences above the charts. Templates on purpose, no LLM: every sentence is traceable to
// the numbers shown below it.

import type { AspectCounts, AspectMatch, AspectSummary } from "./api";

type AspectCopy = {
  label: string;
  short: string;
  narrow?: string;
  phrase: string;
  description: string;
};

// `phrase` is the label as it reads inside a sentence; `short` heads a column, and `narrow`,
// when set, replaces it in a phone-width cell.
export const ASPECT_COPY: Record<string, AspectCopy> = {
  performance: {
    label: "Performance",
    short: "Performance",
    narrow: "Perf.",
    phrase: "performance",
    description: "Frame rate, stutter, loading times and how well it runs",
  },
  price: {
    label: "Price & value",
    short: "Price",
    phrase: "price and value",
    description: "Whether it is worth what it costs",
  },
  bugs: {
    label: "Bugs & stability",
    short: "Bugs",
    phrase: "bugs and stability",
    description: "Crashes, glitches and broken features",
  },
  story: {
    label: "Story & writing",
    short: "Story",
    phrase: "story and writing",
    description: "Plot, characters, dialogue and voice acting",
  },
  gameplay: {
    label: "Gameplay",
    short: "Gameplay",
    phrase: "gameplay",
    description: "Combat, mechanics, content and the core loop",
  },
};

export const copyFor = (aspect: string): AspectCopy =>
  ASPECT_COPY[aspect] ?? { label: aspect, short: aspect, phrase: aspect, description: "" };

// Fewer mentions than this and an aspect's percentage is too noisy to call a strength or a
// weakness (Hades has 12 performance reviews out of 1000; one review moves it 8 points).
export const MIN_MENTIONS = 20;
const FLAT_SPREAD = 10; // best and worst within this many points: no standout either way
const GAP = 5; // Steam's share must beat the weakest aspect by this much for a "but"
const HIGH = 85; // a topic this positive is no weak spot, whatever Steam's share
const STEEP = 20; // a gap this wide, or a topic under half positive, earns an "only"

export const pct = (value: number | null) => (value === null ? null : Math.round(value));

// Steam's store page truncates its percentage (171,921 of 173,897 is 98.86%, shown as 98%), so
// the stored rating is shown the same way to match what visitors see there.
export const storePct = ({ positive, total }: { positive: number; total: number }) =>
  Math.floor((100 * positive) / total);

/** Aspects with enough mentions to judge, weakest first. */
export function judged<T extends AspectCounts>(aspects: T[]): T[] {
  return aspects
    .filter((a) => a.total >= MIN_MENTIONS && a.positive_pct !== null)
    .sort((a, b) => (a.positive_pct ?? 0) - (b.positive_pct ?? 0));
}

export function verdict(summary: { aspects: AspectCounts[] }): string {
  const ranked = judged(summary.aspects);
  if (ranked.length === 0) {
    return "Too few reviews mention specific topics to point out strengths or weaknesses.";
  }
  const worst = ranked[0];
  const best = ranked[ranked.length - 1];
  const bestPct = pct(best.positive_pct)!;
  const worstPct = pct(worst.positive_pct)!;
  if (ranked.length === 1) {
    return `Only ${copyFor(best.aspect).phrase} comes up often enough to judge: ${bestPct}% positive.`;
  }
  if (bestPct - worstPct < FLAT_SPREAD) {
    const mean = Math.round(ranked.reduce((sum, a) => sum + (a.positive_pct ?? 0), 0) / ranked.length);
    return `Players feel much the same about every topic they bring up: around ${mean}% positive.`;
  }
  const weakness =
    worstPct < 40
      ? `${copyFor(worst.aspect).phrase} is the sore point at ${worstPct}%`
      : worstPct < 60
        ? `they are split on ${copyFor(worst.aspect).phrase} (${worstPct}%)`
        : `${copyFor(worst.aspect).phrase} trails at ${worstPct}%`;
  // "Most positive about", not "love": the best aspect can be bugs, where it means few complaints.
  return `Players are most positive about ${copyFor(best.aspect).phrase} (${bestPct}%), but ${weakness}.`;
}

/**
 * The verdict minus its weakest topic, for when the comparison above it already names that:
 * the strongest topic, or the flat-spread sentence. Null when there is nothing to add.
 */
export function strength(summary: { aspects: AspectCounts[] }): string | null {
  const ranked = judged(summary.aspects);
  if (ranked.length < 2) return null;
  const best = ranked[ranked.length - 1];
  const bestPct = pct(best.positive_pct)!;
  if (bestPct - pct(ranked[0].positive_pct)! < FLAT_SPREAD) return verdict(summary);
  return `Players are most positive about ${copyFor(best.aspect).phrase} (${bestPct}%).`;
}

export type Comparison = {
  steamPct: number;
  aspect: string;
  aspectPct: number;
  weaker: boolean; // the aspect is clearly below Steam's share (a "but" sentence)
  text: string;
};

/**
 * The share of reviewers who recommend the game on Steam against the weakest judged aspect,
 * in words scaled to the gap: "but only" for a wide one, "but … less positive" for a
 * narrow one, and "even … holds" when the weakest topic is still high or keeps up (plainly
 * stated when it keeps up below half, where "even" would sound like praise).
 */
export function comparison(
  summary: { aspects: AspectCounts[] } & Pick<AspectSummary, "steam_sample">,
): Comparison | null {
  const steamPct = pct(summary.steam_sample.positive_pct);
  const [worst] = judged(summary.aspects);
  if (steamPct === null || !worst) return null;
  const aspectPct = pct(worst.positive_pct)!;
  const phrase = copyFor(worst.aspect).phrase;
  const gap = steamPct - aspectPct;
  const weaker = gap >= GAP && aspectPct < HIGH;
  const steam = `${steamPct}% of reviewers recommend it on Steam`;
  const text = !weaker
    ? aspectPct >= 50
      ? `${steam}, and even ${phrase}, the lowest-rated topic, reads ${aspectPct}% positive.`
      : `${steam}; ${phrase}, the lowest-rated topic, reads ${aspectPct}% positive.`
    : gap >= STEEP || aspectPct < 50
      ? `${steam}, but only ${aspectPct}% of reviews about ${phrase} read as positive.`
      : `${steam}, but reviews about ${phrase} are less positive: ${aspectPct}%.`;
  return { steamPct, aspect: worst.aspect, aspectPct, weaker, text };
}

// UTC: trend dates are UTC days, and review timestamps are grouped into them in UTC.
const DATE = new Intl.DateTimeFormat("en-US", {
  month: "short",
  day: "numeric",
  year: "numeric",
  timeZone: "UTC",
});

/** "2026-09-23" or a full ISO timestamp -> "Sep 23, 2026", as its UTC day. */
export const formatDate = (iso: string) => DATE.format(new Date(iso));

const RELATIVE = new Intl.RelativeTimeFormat("en-US", { numeric: "auto" });
const DAY_MS = 86_400_000;
const localMidnight = (ms: number) => new Date(ms).setHours(0, 0, 0, 0);

/** A past timestamp as calendar days in local time: "today", "yesterday", "3 weeks ago". */
export function timeAgo(iso: string, now = Date.now()): string {
  // Rounded, since a day across a DST change is 23 or 25 hours. Capped at today, so a client
  // clock behind the server's never says "tomorrow".
  const days = Math.min(0, Math.round((localMidnight(Date.parse(iso)) - localMidnight(now)) / DAY_MS));
  if (days > -7) return RELATIVE.format(days, "day");
  if (days > -30) return RELATIVE.format(Math.round(days / 7), "week");
  if (days > -365) return RELATIVE.format(Math.round(days / 30), "month");
  return RELATIVE.format(Math.round(days / 365), "year");
}

/** Quotes are clauses cut from a longer review; one starting mid-sentence gets an ellipsis. */
export const quoteText = (text: string) => (/^[a-z]/.test(text) ? `…${text}` : text);

const SPIKE_MIN = 20; // negative reviews in a day before a day can stand out at all
const SPIKE_RATIO = 5; // times the median day's negative reviews

export type Spike = { date: string; negative: number; typical: number; text: string };

/**
 * The day with the most negative reviews, when it dwarfs a typical day: something happened
 * then (a patch, a sale, a controversy), which the chart alone leaves unsaid. The cause is
 * not known here, so the sentence states only the numbers.
 */
export function spike(trend: AspectSummary["trend"]): Spike | null {
  if (trend.length === 0) return null;
  const sorted = trend.map((d) => d.negative).sort((a, b) => a - b);
  const typical = sorted[Math.floor(sorted.length / 2)];
  const peak = trend.reduce((max, d) => (d.negative > max.negative ? d : max));
  if (peak.negative < SPIKE_MIN || peak.negative < SPIKE_RATIO * Math.max(typical, 1)) return null;
  return {
    date: peak.date,
    negative: peak.negative,
    typical,
    text: `${formatDate(peak.date)} stands out: ${peak.negative} negative reviews in one day, against ${typical} on a typical day.`,
  };
}

export type Segment = { text: string; aspect: string | null };

/**
 * A review's text cut into plain runs and the sentences its topics were matched on, so the
 * list can mark them. A match not found verbatim (the splitter drops BBCode) is skipped, as
 * is one overlapping an earlier match.
 */
export function highlight(text: string, matches: AspectMatch[]): Segment[] {
  const spans: { start: number; end: number; aspect: string }[] = [];
  let from = 0;
  for (const m of matches) {
    // Matches come in review order, so each is searched for after the previous one.
    const start = text.indexOf(m.text, from);
    if (start < 0 || !m.text) continue;
    const end = start + m.text.length;
    if (spans.some((s) => start < s.end && end > s.start)) continue;
    spans.push({ start, end, aspect: m.aspect });
    from = end;
  }
  spans.sort((a, b) => a.start - b.start);
  const out: Segment[] = [];
  let at = 0;
  for (const s of spans) {
    if (s.start > at) out.push({ text: text.slice(at, s.start), aspect: null });
    out.push({ text: text.slice(s.start, s.end), aspect: s.aspect });
    at = s.end;
  }
  if (at < text.length) out.push({ text: text.slice(at), aspect: null });
  return out;
}
