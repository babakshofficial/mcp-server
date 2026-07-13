import { useEffect, useMemo, useState } from "react";

import { Change, ProjectState, fetchChangelog, fetchState } from "../lib/api";

export function useSyncData(filters: { team: string; type: string }) {
  const [state, setState] = useState<ProjectState | null>(null);
  const [changes, setChanges] = useState<Change[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  async function refresh() {
    try {
      setError(null);
      const [nextState, nextChanges] = await Promise.all([fetchState(), fetchChangelog(filters)]);
      setState(nextState);
      setChanges(nextChanges);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to load dashboard data");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    refresh();
  }, [filters.team, filters.type]);

  useEffect(() => {
    const source = new EventSource("/api/events");
    source.addEventListener("change", () => {
      refresh();
    });
    return () => source.close();
  }, [filters.team, filters.type]);

  const changeTypes = useMemo(
    () => Array.from(new Set([...(state?.recent_changes ?? []), ...changes].map((change) => change.type))).sort(),
    [state, changes]
  );

  return { state, changes, changeTypes, loading, error, refresh };
}
