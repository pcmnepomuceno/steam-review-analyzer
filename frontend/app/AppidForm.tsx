"use client";

import { useRouter } from "next/navigation";
import { useEffect, useId, useRef, useState } from "react";

import { fetchState, type Loaded, type SearchResult } from "../lib/api";

const DEBOUNCE_MS = 300; // one /steam/search call after typing stops, not one per key
const MIN_CHARS = 2; // the API's own minimum

/**
 * Search Steam by name or appid, with a suggestion list (an ARIA combobox). Picking any result
 * opens /games/{appid}; the dashboard there shows whatever the API answers, including the
 * "not in this demo" screen for a game that was not analyzed ahead of time. Each suggestion
 * says which of those it will be, and games with results come first.
 */
export default function AppidForm({ analyzed }: { analyzed?: Set<number> }) {
  const router = useRouter();
  const listId = useId();
  const [query, setQuery] = useState("");
  // The answer remembers its term, so a stale answer is never shown for a newer query.
  const [answer, setAnswer] = useState<{ term: string; result: Loaded<SearchResult[]> } | null>(
    null,
  );
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(-1);
  // A name submitted before its search answered: opened as soon as the answer arrives.
  const submitted = useRef<string | null>(null);

  const term = query.trim();
  const searchable = term.length >= MIN_CHARS;

  useEffect(() => {
    if (!searchable) return;
    const controller = new AbortController();
    const timer = setTimeout(() => {
      fetchState<SearchResult[]>(`/api/steam/search?q=${encodeURIComponent(term)}`, controller.signal)
        .then((result) => {
          setAnswer({ term, result });
          if (submitted.current !== term) return;
          submitted.current = null;
          if (result.kind === "ready" && result.data[0]) {
            setOpen(false);
            router.push(`/games/${result.data[0].appid}`);
          }
        })
        .catch(() => {}); // aborted by a newer term or unmount
    }, DEBOUNCE_MS);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [term, searchable, router]);

  const current = searchable && answer?.term === term ? answer.result : null;
  const results =
    current?.kind === "ready"
      ? current.data
          .map((r) => ({ ...r, availability: availabilityOf(r, analyzed) }))
          // Stable: Steam's relevance order within each group.
          .sort((a, b) => Number(b.availability === "analyzed") - Number(a.availability === "analyzed"))
          .slice(0, 8)
      : [];
  const blocked = results.some((r) => r.availability === "unavailable");
  const expanded = open && results.length > 0;
  const digits = /^\d+$/.test(term) ? Number(term) : null;

  const go = (appid: number) => {
    setOpen(false);
    router.push(`/games/${appid}`);
  };

  let status = "";
  if (searchable && !current) status = "Searching Steam…";
  else if (current?.kind === "ready" && results.length === 0)
    status = digits ? `No Steam game found for “${term}”; press Enter to try appid ${digits} anyway.` : `No Steam games match “${term}”.`;
  else if (current && current.kind !== "ready")
    status = "Steam search is unavailable right now. You can still enter an appid.";
  else if (expanded)
    status =
      `${results.length} suggestion${results.length === 1 ? "" : "s"}; use the arrow keys to choose.` +
      (blocked ? " This demo opens only the games marked Analyzed." : "");
  else if (!term) status = "For example Hades, or its appid 1145360 from the store URL.";

  return (
    <div className="search">
      <form
        role="search"
        onSubmit={(e) => {
          e.preventDefault();
          const picked = results[active] ?? (digits === null ? results[0] : undefined);
          if (picked) go(picked.appid);
          else if (digits !== null && digits > 0) go(digits);
          else if (searchable && !current) submitted.current = term; // still searching
        }}
      >
        <div className="field">
          <input
            type="search"
            inputMode="search"
            enterKeyHint="search"
            autoComplete="off"
            autoCapitalize="none"
            autoCorrect="off"
            spellCheck={false}
            role="combobox"
            aria-label="Search Steam by game name or appid"
            aria-autocomplete="list"
            aria-expanded={expanded}
            aria-controls={listId}
            aria-activedescendant={expanded && active >= 0 ? `${listId}-${active}` : undefined}
            placeholder="Search Steam for a game"
            value={query}
            onChange={(e) => {
              setQuery(e.target.value);
              submitted.current = null;
              setOpen(true);
              setActive(-1);
            }}
            onFocus={() => setOpen(true)}
            onBlur={() => setOpen(false)}
            onKeyDown={(e) => {
              if (e.key === "ArrowDown" || e.key === "ArrowUp") {
                if (!results.length) return;
                e.preventDefault();
                setOpen(true);
                const step = e.key === "ArrowDown" ? 1 : -1;
                const n = results.length;
                setActive((i) => (i < 0 ? (step > 0 ? 0 : n - 1) : (i + step + n) % n));
              } else if (e.key === "Escape") {
                if (expanded) {
                  e.preventDefault();
                  setOpen(false);
                  setActive(-1);
                }
              }
            }}
          />
          <ul id={listId} role="listbox" className="suggestions" hidden={!expanded} aria-label="Matching Steam games">
            {results.map((r, i) => (
              <li
                key={r.appid}
                id={`${listId}-${i}`}
                role="option"
                aria-selected={i === active}
                // mousedown would blur the input (closing the list) before the click lands
                onMouseDown={(e) => e.preventDefault()}
                onClick={() => go(r.appid)}
                onMouseEnter={() => setActive(i)}
              >
                {r.image ? (
                  // eslint-disable-next-line @next/next/no-img-element -- Steam CDN thumbnails, no optimizer needed
                  <img src={r.image} alt="" width={92} height={34} loading="lazy" />
                ) : (
                  <span className="no-image" />
                )}
                <span className="text">
                  <span className="name">{r.name}</span>
                  <span className="sub">
                    appid {r.appid}
                    {r.availability && (
                      <span className={`tag${r.availability === "analyzed" ? "" : " dim"}`}>
                        {AVAILABILITY_LABEL[r.availability]}
                      </span>
                    )}
                  </span>
                </span>
              </li>
            ))}
          </ul>
        </div>
        {/* "Open", not "Analyze": a deployment without on-demand processing only opens games
            analyzed ahead of time, and says so for any other. */}
        <button type="submit" className="primary">
          Open
        </button>
      </form>
      <p className="search-status" aria-live="polite">
        {status}
      </p>
    </div>
  );
}

const AVAILABILITY_LABEL = {
  analyzed: "Analyzed",
  on_demand: "Not analyzed yet",
  unavailable: "Not in this demo",
} as const;

/** The API's answer, or for an API older than that field, whether the homepage lists it. */
function availabilityOf(r: SearchResult, analyzed?: Set<number>): SearchResult["availability"] {
  if (r.availability) return r.availability;
  if (!analyzed) return undefined;
  return analyzed.has(r.appid) ? "analyzed" : "on_demand";
}
