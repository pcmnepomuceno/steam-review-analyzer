"use client";

import { useSearchParams } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import {
  fetchState,
  pageInfo,
  type Loaded,
  type Review,
  type ReviewPage,
  type ReviewSort,
} from "../../../lib/api";
import { copyFor, formatDate, highlight } from "../../../lib/insights";
import Thumb from "./Thumb";

const PAGE_SIZE = 20;

// The first is the default, left out of the URL. Most helpful first: the newest reviews of a
// game in the news are often off-topic noise (Cities: Skylines II's Sep 23 review bomb).
const SORTS: [ReviewSort, string][] = [
  ["helpful", "Most helpful"],
  ["newest", "Newest"],
  ["playtime", "Most played"],
];

const playtime = (minutes: number) =>
  minutes < 60 ? "under 1 h played" : `${Math.round(minutes / 60).toLocaleString("en-US")} h played`;

export default function ReviewList({ appid, aspects }: { appid: number; aspects: string[] }) {
  // Filters, sort and page live in the URL (?aspect=bugs&sentiment=negative&offset=20), so a
  // filtered view can be linked to and survives a reload.
  // Unknown values (a stale or hand-edited link) are ignored rather than sent on for a 422.
  const search = useSearchParams();
  const rawAspect = search.get("aspect") ?? "";
  const aspect = aspects.includes(rawAspect) ? rawAspect : "";
  const rawSentiment = search.get("sentiment") ?? "";
  const sentiment = ["positive", "negative"].includes(rawSentiment) ? rawSentiment : "";
  const rawSort = search.get("sort");
  const sort = SORTS.find(([key]) => key === rawSort)?.[0] ?? SORTS[0][0];
  const rawDay = search.get("day") ?? "";
  // A real UTC day, as in the trend: 2026-02-30 doesn't survive the round trip through Date.
  const day = isUtcDay(rawDay) ? rawDay : "";
  const rawOffset = Number(search.get("offset"));
  // Snapped to a page boundary, so "Page N of M" matches the reviews shown.
  const offset =
    Number.isSafeInteger(rawOffset) && rawOffset > 0 ? rawOffset - (rawOffset % PAGE_SIZE) : 0;
  const navigate = (changes: Record<string, string | number>) => {
    const next = new URLSearchParams(search);
    for (const [key, value] of Object.entries(changes)) {
      if (value) next.set(key, String(value));
      else next.delete(key);
    }
    // pushState, not replaceState: Back steps through filter and page changes.
    window.history.pushState(null, "", `${window.location.pathname}${next.size ? `?${next}` : ""}#reviews`);
  };
  // The answer remembers which query it belongs to; a mismatch means a newer one is loading.
  const [answer, setAnswer] = useState<{ query: string; result: Loaded<ReviewPage> } | null>(null);

  const params = new URLSearchParams({ limit: String(PAGE_SIZE), offset: String(offset), sort });
  if (aspect) params.set("aspect", aspect);
  if (sentiment) params.set("sentiment", sentiment);
  if (day) params.set("day", day);
  const query = `/api/games/${appid}/reviews?${params}`;
  const result = answer?.result ?? null;
  const loading = answer?.query !== query;

  useEffect(() => {
    const controller = new AbortController();
    fetchState<ReviewPage>(query, controller.signal)
      .then((r) => setAnswer({ query, result: r }))
      .catch(() => {}); // aborted by a newer filter/page or unmount
    return () => controller.abort();
  }, [query]);

  const page = result?.kind === "ready" ? result.data : null;
  const info = page && pageInfo(page.total, PAGE_SIZE, page.offset);
  const status = !result
    ? "Loading reviews…"
    : !page || !info
      ? ""
      : page.total === 0
        ? "No reviews match these filters."
        : info.to === 0
          ? "No reviews on this page."
          : `Showing ${info.from}–${info.to} of ${page.total.toLocaleString("en-US")}`;

  return (
    <>
      <div className="filters">
        <label>
          Topic
          <select value={aspect} onChange={(e) => navigate({ aspect: e.target.value, offset: 0 })}>
            <option value="">All reviews</option>
            {aspects.map((a) => (
              <option key={a} value={a}>
                {copyFor(a).label}
              </option>
            ))}
          </select>
        </label>
        <label>
          Model reads as
          <select
            value={sentiment}
            onChange={(e) => navigate({ sentiment: e.target.value, offset: 0 })}
          >
            <option value="">Either</option>
            <option value="positive">Positive</option>
            <option value="negative">Negative</option>
          </select>
        </label>
        <label>
          Sort
          <select
            value={sort}
            onChange={(e) => navigate({ sort: e.target.value === SORTS[0][0] ? "" : e.target.value, offset: 0 })}
          >
            {SORTS.map(([key, name]) => (
              <option key={key} value={key}>
                {name}
              </option>
            ))}
          </select>
        </label>
        {day && (
          <div className="day-filter">
            <span>Day</span>
            <button
              type="button"
              onClick={() => navigate({ day: "", offset: 0 })}
              aria-label={`Remove the day filter, ${formatDate(day)}`}
            >
              {formatDate(day)}
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.25" strokeLinecap="round" aria-hidden>
                <path d="M18 6 6 18M6 6l12 12" />
              </svg>
            </button>
          </div>
        )}
        {(aspect || sentiment || day) && (
          <button
            type="button"
            className="clear"
            onClick={() => navigate({ aspect: "", sentiment: "", day: "", offset: 0 })}
          >
            Clear filters
          </button>
        )}
      </div>

      {result && result.kind !== "ready" && (
        <p role="alert">
          {result.kind === "processing" || result.kind === "timeout"
            ? "Reviews are still being analyzed; reload shortly."
            : "message" in result
              ? result.message
              : "Could not load reviews."}
        </p>
      )}
      <p className="list-status muted" aria-live="polite">
        {status}
      </p>

      {page && info && (
        <>
          <ul className={`reviews${loading ? " stale" : ""}`} aria-busy={loading}>
            {page.items.map((r) => (
              <ReviewItem key={r.id} review={r} topic={aspect} />
            ))}
          </ul>
          {(info.hasPrev || info.hasNext) && (
            <div className="pager">
              <button
                disabled={!info.hasPrev || loading}
                onClick={() => navigate({ offset: info.prevOffset })}
              >
                <span aria-hidden>←</span> Previous
              </button>
              {info.to > 0 && (
                <span className="muted">
                  Page {page.offset / PAGE_SIZE + 1} of {Math.ceil(page.total / PAGE_SIZE)}
                </span>
              )}
              <button
                disabled={!info.hasNext || loading}
                onClick={() => navigate({ offset: page.offset + PAGE_SIZE })}
              >
                Next <span aria-hidden>→</span>
              </button>
            </div>
          )}
        </>
      )}
    </>
  );
}

function ReviewItem({ review: r, topic }: { review: Review; topic: string }) {
  const predicted = r.predicted_sentiment;
  // The model reads the words, Steam records the thumb; where they part is worth showing.
  const disagree = predicted !== null && (predicted === "positive") !== r.voted_up;
  const facts = `${formatDate(r.created_at)} · ${playtime(r.playtime_forever)}`;
  return (
    <li>
      {/* A rail beside the text on wide screens: the verdicts first, the facts under them. */}
      <div className="review-meta">
        <span className={`vote ${r.voted_up ? "up" : "down"}`}>
          <Thumb up={r.voted_up} />
          {r.voted_up ? "Recommended" : "Not recommended"}
        </span>
        {predicted && <span className={`badge ${predicted}`}>Model: {predicted}</span>}
        {disagree && <span className="disagree">Model disagrees with the reviewer</span>}
        <span className="facts">
          {facts}
          {r.votes_up > 0 && ` · ${r.votes_up.toLocaleString("en-US")} found it helpful`}
        </span>
      </div>
      <div className="review-body">
        {r.aspects.length > 0 && (
          <p className="review-topics">
            <span className="sr-only">Topics: </span>
            {/* The filtered topic first and stronger, so the card says why it is listed. */}
            {[...r.aspects]
              .sort((a, b) => Number(b === topic) - Number(a === topic))
              .map((a) => (
                <span key={a} className={`tag${a === topic ? " active" : ""}`}>
                  {copyFor(a).label}
                </span>
              ))}
          </p>
        )}
        <ReviewText review={r} context={facts} topic={topic} />
      </div>
    </li>
  );
}

/** A review clamped to 6 lines by CSS, with a toggle only when the clamp hides something. */
function ReviewText({
  review,
  context,
  topic,
}: {
  review: Review;
  context: string;
  topic: string;
}) {
  const ref = useRef<HTMLParagraphElement>(null);
  const [open, setOpen] = useState(false);
  const [clamped, setClamped] = useState(false);
  const id = `review-${review.id}`;

  // Measured, not guessed from length: how many lines a review wraps to depends on the width.
  // Re-measured on resize; while open there is no clamp to measure, so the last answer stands.
  useEffect(() => {
    const el = ref.current!;
    const observer = new ResizeObserver(() => {
      if (!el.classList.contains("full")) setClamped(el.scrollHeight > el.clientHeight);
    });
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  return (
    <>
      <p ref={ref} id={id} className={`review-text${open ? " full" : ""}`}>
        {/* The sentences the topic tags came from, so a tag can be checked against its source. */}
        {highlight(review.review_text, review.matches ?? []).map((s, i) =>
          s.aspect ? (
            <mark key={i} className={s.aspect === topic ? "on" : undefined} title={copyFor(s.aspect).label}>
              {s.text}
            </mark>
          ) : (
            s.text
          ),
        )}
      </p>
      {(clamped || open) && (
        <button
          type="button"
          className="link"
          aria-expanded={open}
          aria-controls={id}
          onClick={() => setOpen(!open)}
        >
          {open ? "Show less" : "Show full review"}
          <span className="sr-only">, {context}</span>
        </button>
      )}
    </>
  );
}

function isUtcDay(value: string): boolean {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
  const date = new Date(`${value}T00:00:00Z`);
  return !Number.isNaN(date.getTime()) && date.toISOString().slice(0, 10) === value;
}
