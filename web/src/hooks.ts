import { useCallback, useEffect, useLayoutEffect, useRef, useState, type RefObject } from "react";
import { ApiError, getJson, type Endpoint } from "./api";

/* ------------------------------------------------------------------ loading */

export interface Load<T> {
  /** The document, once it has arrived. With `keepPrevious`, the last one stays while the next loads. */
  data: T | undefined;
  error: ApiError | undefined;
  pending: boolean;
  retry: () => void;
}

interface LoadState<T> {
  url: string | null;
  data?: T;
  error?: ApiError;
  pending: boolean;
}

/** Documents that do not change while the page is open (a run's saved files), by URL. */
const documentCache = new Map<string, unknown>();

function toApiError(error: unknown, url: string): ApiError {
  if (error instanceof ApiError) return error;
  return new ApiError(
    "parse",
    "This page could not read the answer from the local API.",
    error instanceof Error ? `${error.name}: ${error.message}` : String(error),
    url,
  );
}

/**
 * Fetch an endpoint and track pending, failed and loaded states.
 * A superseded request is aborted, so a slow answer can never overwrite a newer one.
 */
export function useLoad<T>(
  target: Endpoint<T> | null,
  options: { cache?: boolean; keepPrevious?: boolean } = {},
): Load<T> {
  const url = target?.url ?? null;
  const { cache = false, keepPrevious = false } = options;
  const [attempt, setAttempt] = useState(0);
  const [state, setState] = useState<LoadState<T>>({ url: null, pending: false });

  useEffect(() => {
    if (url === null) {
      setState({ url: null, pending: false });
      return;
    }
    if (cache && documentCache.has(url)) {
      setState({ url, data: documentCache.get(url) as T, pending: false });
      return;
    }
    const controller = new AbortController();
    setState((previous) =>
      keepPrevious && previous.data !== undefined ? { url, data: previous.data, pending: true } : { url, pending: true },
    );
    getJson<T>(url, controller.signal).then(
      (data) => {
        if (controller.signal.aborted) return;
        if (cache) documentCache.set(url, data);
        setState({ url, data, pending: false });
      },
      (error: unknown) => {
        if (controller.signal.aborted) return;
        setState({ url, error: toApiError(error, url), pending: false });
      },
    );
    return () => controller.abort();
  }, [url, cache, keepPrevious, attempt]);

  const retry = useCallback(() => setAttempt((count) => count + 1), []);

  // Between a URL change and the effect that starts its request, `state` still
  // describes the previous URL. Never present that as the answer to this one.
  const current = state.url === url;
  const showData = current || (keepPrevious && url !== null);
  return {
    data: showData ? state.data : undefined,
    error: current ? state.error : undefined,
    pending: url !== null && (!current || state.pending),
    retry,
  };
}

/* ------------------------------------------------------------------ routing */

export const VIEWS = ["overview", "timeline", "comparison", "engineering"] as const;
export type ViewId = (typeof VIEWS)[number];

export interface Route {
  view: ViewId;
  params: Readonly<Record<string, string>>;
}

/** "#/timeline?run=a&patient=b" -> { view: "timeline", params: { run: "a", patient: "b" } }. */
export function parseHash(hash: string): Route {
  const text = hash.replace(/^#\/?/, "");
  const question = text.indexOf("?");
  const name = question === -1 ? text : text.slice(0, question);
  const params: Record<string, string> = {};
  if (question !== -1) {
    for (const [key, value] of new URLSearchParams(text.slice(question + 1))) params[key] = value;
  }
  const view = (VIEWS as readonly string[]).includes(name) ? (name as ViewId) : "overview";
  return { view, params };
}

export function formatHash(view: ViewId, params: Readonly<Record<string, string | undefined>> = {}): string {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== "") query.set(key, value);
  }
  const text = query.toString();
  return `#/${view}${text ? `?${text}` : ""}`;
}

export type Navigate = (
  view: ViewId,
  params: Readonly<Record<string, string | undefined>>,
  mode?: "push" | "replace",
) => void;

/** The current view and its parameters, kept in the URL hash so a view can be linked and reloaded. */
export function useRoute(): { route: Route; navigate: Navigate } {
  const [route, setRoute] = useState<Route>(() => parseHash(window.location.hash));

  useEffect(() => {
    // Links change the hash; back and forward fire popstate for entries made with pushState.
    const onChange = () => setRoute(parseHash(window.location.hash));
    window.addEventListener("hashchange", onChange);
    window.addEventListener("popstate", onChange);
    return () => {
      window.removeEventListener("hashchange", onChange);
      window.removeEventListener("popstate", onChange);
    };
  }, []);

  const navigate = useCallback<Navigate>((view, params, mode = "push") => {
    const hash = formatHash(view, params);
    if (hash === window.location.hash) return;
    if (mode === "replace") window.history.replaceState(null, "", hash);
    else window.history.pushState(null, "", hash);
    setRoute(parseHash(hash));
  }, []);

  return { route, navigate };
}

/* -------------------------------------------------------------------- misc */

/** The value, once it has stopped changing for `delay` milliseconds. */
export function useDebounced<T>(value: T, delay: number): T {
  const [settled, setSettled] = useState(value);
  useEffect(() => {
    const timer = window.setTimeout(() => setSettled(value), delay);
    return () => window.clearTimeout(timer);
  }, [value, delay]);
  return settled;
}

/** The rendered width of an element in CSS pixels, kept current as the layout changes. */
export function useElementWidth<T extends HTMLElement>(): [RefObject<T | null>, number] {
  const ref = useRef<T | null>(null);
  const [width, setWidth] = useState(0);
  useLayoutEffect(() => {
    const element = ref.current;
    if (!element) return;
    const measure = () => setWidth(Math.floor(element.getBoundingClientRect().width));
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    return () => observer.disconnect();
  }, []);
  return [ref, width];
}
