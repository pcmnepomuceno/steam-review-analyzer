// Types for the FastAPI responses and the one place that turns an HTTP answer into a UI state.

export type Sentiment = "positive" | "negative";

export type SentimentCounts = {
  positive: number;
  negative: number;
  total: number;
  positive_pct: number | null;
};

// `voted_up` is the reviewer's thumb on Steam; null or absent for quotes stored before it.
export type Quote = { text: string; sentiment: Sentiment; voted_up?: boolean | null };

export type AspectCounts = SentimentCounts & { aspect: string };

export type AspectStats = AspectCounts & { quotes: Quote[] };

// Steam's own store rating, as its store page shows it: English reviews, all time.
export type SteamRating = {
  score_desc: string; // e.g. "Very Positive"
  positive: number;
  total: number;
  positive_pct: number | null;
};

export type AspectSummary = {
  appid: number;
  name: string;
  status: "ready";
  overall: SentimentCounts;
  aspects: AspectStats[];
  trend: { date: string; positive: number; negative: number }[];
  steam_sample: SentimentCounts; // Steam's thumbs (voted_up) on the same reviews as `overall`
  steam_rating: SteamRating | null; // null until the API could fetch it from Steam
  disagreements?: number; // reviews where the model's call and the reviewer's thumb differ
};

export type AspectMatch = { aspect: string; text: string };

export type ReviewSort = "helpful" | "newest" | "playtime";

export type Review = {
  id: number;
  review_text: string;
  voted_up: boolean;
  votes_up: number;
  playtime_forever: number; // minutes
  language: string;
  created_at: string;
  predicted_sentiment: Sentiment | null;
  aspects: string[];
  matches?: AspectMatch[]; // the sentences behind `aspects`; absent from an older API
};

export type ReviewPage = {
  appid: number;
  total: number;
  limit: number;
  offset: number;
  items: Review[];
};

export type AvailableGame = { appid: number; name: string };

// GET /games: every game whose results are ready, with the counts its card's comparison uses.
export type GameListItem = AvailableGame & {
  review_count: number;
  analyzed_at: string | null;
  aspects?: AspectCounts[]; // absent from an API deployed before the card counts
  steam_sample?: SentimentCounts;
};

// GET /steam/search
// `availability` is absent from an API older than it.
export type SearchResult = {
  appid: number;
  name: string;
  image: string | null;
  availability?: "analyzed" | "on_demand" | "unavailable";
};

/** Steam's wide store banner for an app; not every app has one (hide it on error). */
export const headerImage = (appid: number) =>
  `https://shared.akamai.steamstatic.com/store_item_assets/steam/apps/${appid}/header.jpg`;

export type Loaded<T> =
  | { kind: "ready"; data: T }
  | { kind: "processing" } // 202: analysis running
  // 500 status "failed". `message` names the reason for an interrupted run, else it is a
  // generic line. `retryable`: this API analyzes on request, so the user can ask for a rerun
  // (POST /games/{appid}/retry); otherwise only the owner's --force can. `outOfMemory`: the
  // stored error is Python's own MemoryError. An interrupted run is not flagged, since an
  // OS/Docker out-of-memory kill leaves the same reason as Ctrl-C or `docker stop`.
  | { kind: "failed"; message: string; retryable: boolean; outOfMemory: boolean }
  | { kind: "not_found"; message: string } // 404: Steam has no such app
  // 403 status "unavailable": this deployment only serves games processed ahead of time
  | { kind: "unavailable"; message: string; games: AvailableGame[] }
  | { kind: "timeout" } // still processing after maxWaitMs
  | { kind: "error"; status: number | null; message: string }; // 422, 502, 503, network, ...

export async function fetchState<T>(path: string, signal?: AbortSignal): Promise<Loaded<T>> {
  let res: Response;
  try {
    res = await fetch(path, { signal, cache: "no-store" });
  } catch (err) {
    if (signal?.aborted) throw err;
    return { kind: "error", status: null, message: "Could not reach the API." };
  }
  const body = await res.json().catch(() => null);
  if (signal?.aborted) throw signal.reason; // aborted while the body was streaming
  if (res.status === 200) {
    if (body === null) return { kind: "error", status: 200, message: "The API sent an unreadable answer." };
    return { kind: "ready", data: body as T };
  }
  if (res.status === 202) return { kind: "processing" };
  const message = typeof body?.detail === "string" ? body.detail : `Request failed (${res.status})`;
  if (res.status === 404) return { kind: "not_found", message };
  if (res.status === 403 && body?.status === "unavailable") {
    const games = Array.isArray(body.available) ? (body.available as AvailableGame[]) : [];
    return { kind: "unavailable", message, games };
  }
  if (res.status === 500 && body?.status === "failed") {
    return {
      kind: "failed",
      message,
      retryable: body.retryable === true,
      outOfMemory: body.out_of_memory === true,
    };
  }
  return { kind: "error", status: res.status, message };
}

export type PollOptions = {
  intervalMs?: number;
  maxWaitMs?: number;
  signal?: AbortSignal;
  onUpdate?: (state: Loaded<unknown>) => void;
};

/** Fetch `path` until it stops answering 202; every answer goes to `onUpdate`. */
export async function poll<T>(
  path: string,
  { intervalMs = 3000, maxWaitMs = 5 * 60_000, signal, onUpdate }: PollOptions = {},
): Promise<Loaded<T>> {
  const deadline = Date.now() + maxWaitMs;
  for (;;) {
    const state = await fetchState<T>(path, signal);
    if (state.kind !== "processing") {
      onUpdate?.(state);
      return state;
    }
    // The API fails a game whose run died, but a run can legitimately outlast any wait here.
    if (Date.now() + intervalMs > deadline) {
      onUpdate?.({ kind: "timeout" });
      return { kind: "timeout" };
    }
    onUpdate?.(state);
    await sleep(intervalMs, signal);
  }
}

function sleep(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) return reject(signal.reason);
    const timer = setTimeout(resolve, ms);
    signal?.addEventListener("abort", () => {
      clearTimeout(timer);
      reject(signal.reason);
    }, { once: true });
  });
}

export function pageInfo(total: number, limit: number, offset: number) {
  const past = offset >= total; // empty result, or an offset beyond the last page (hand-edited URL)
  return {
    from: past ? 0 : offset + 1,
    to: past ? 0 : Math.min(offset + limit, total),
    hasPrev: offset > 0,
    // From past the end, jump back to the real last page instead of stepping through empty ones.
    prevOffset: past ? Math.max(0, Math.floor((total - 1) / limit) * limit) : Math.max(0, offset - limit),
    hasNext: offset + limit < total,
  };
}
