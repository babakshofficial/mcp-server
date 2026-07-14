import { useEffect, useMemo, useState } from "react";

import {
  Change,
  ProjectState,
  ProjectSummary,
  UserPublic,
  clearSession,
  createProject,
  eventsUrl,
  fetchChangelog,
  fetchHubSettings,
  fetchMe,
  fetchProjects,
  fetchState,
  getStoredToken,
  getStoredUser,
  setSession,
  updateHubSettings,
  updateProject
} from "../lib/api";

export function useAuth() {
  const [user, setUser] = useState<UserPublic | null>(() => getStoredUser());
  const [loading, setLoading] = useState(Boolean(getStoredToken()));
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const token = getStoredToken();
    if (!token) {
      setLoading(false);
      return;
    }
    fetchMe()
      .then((me) => {
        setUser(me);
        setSession(token, me);
      })
      .catch(() => {
        clearSession();
        setUser(null);
      })
      .finally(() => setLoading(false));
  }, []);

  function logout() {
    clearSession();
    setUser(null);
  }

  return { user, loading, error, setError, setUser, logout, isAdmin: user?.hub_role === "admin" };
}

export function useProjects(enabled: boolean) {
  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const [loading, setLoading] = useState(enabled);
  const [error, setError] = useState<string | null>(null);
  const [settings, setSettings] = useState<{ poll_interval_seconds: number; auto_sync_enabled: boolean } | null>(
    null
  );

  async function refresh() {
    if (!enabled) return;
    try {
      setError(null);
      const [nextProjects, nextSettings] = await Promise.all([fetchProjects(), fetchHubSettings()]);
      setProjects(nextProjects);
      setSettings(nextSettings);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to load projects");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    if (!enabled) return;
    refresh();
    const source = new EventSource(eventsUrl());
    source.addEventListener("change", () => {
      refresh();
    });
    return () => source.close();
  }, [enabled]);

  async function addProject(input: {
    name: string;
    description?: string;
    openapi_url?: string;
    auto_sync?: boolean;
  }) {
    const created = await createProject(input);
    await refresh();
    return created;
  }

  async function saveSettings(input: { poll_interval_seconds?: number; auto_sync_enabled?: boolean }) {
    const next = await updateHubSettings(input);
    setSettings(next);
    return next;
  }

  async function saveProject(
    projectId: string,
    input: {
      openapi_url?: string;
      auto_sync?: boolean;
      sync_mode?: "interval" | "on_commit";
      git_repo_path?: string;
      name?: string;
      description?: string;
    }
  ) {
    await updateProject(projectId, input);
    await refresh();
  }

  return { projects, settings, loading, error, refresh, addProject, saveSettings, saveProject };
}

export function useSyncData(projectId: string | null, filters: { team: string; type: string }, enabled: boolean) {
  const [state, setState] = useState<ProjectState | null>(null);
  const [changes, setChanges] = useState<Change[]>([]);
  const [loading, setLoading] = useState(Boolean(projectId) && enabled);
  const [error, setError] = useState<string | null>(null);

  async function refresh() {
    if (!projectId || !enabled) {
      setState(null);
      setChanges([]);
      setLoading(false);
      return;
    }
    try {
      setError(null);
      const [nextState, nextChanges] = await Promise.all([
        fetchState(projectId),
        fetchChangelog(projectId, filters)
      ]);
      setState(nextState);
      setChanges(nextChanges);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to load dashboard data");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    setLoading(Boolean(projectId) && enabled);
    refresh();
  }, [projectId, filters.team, filters.type, enabled]);

  useEffect(() => {
    if (!projectId || !enabled) return;
    const source = new EventSource(eventsUrl());
    source.addEventListener("change", (event) => {
      try {
        const payload = JSON.parse((event as MessageEvent).data || "{}");
        if (!payload.project_id || payload.project_id === projectId) {
          refresh();
        }
      } catch {
        refresh();
      }
    });
    return () => source.close();
  }, [projectId, filters.team, filters.type, enabled]);

  const changeTypes = useMemo(
    () => Array.from(new Set([...(state?.recent_changes ?? []), ...changes].map((change) => change.type))).sort(),
    [state, changes]
  );

  return { state, changes, changeTypes, loading, error, refresh };
}
