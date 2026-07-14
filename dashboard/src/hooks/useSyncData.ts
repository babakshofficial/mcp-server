import { useEffect, useMemo, useState } from "react";

import {
  Change,
  ProjectState,
  ProjectSummary,
  createProject,
  fetchChangelog,
  fetchHubSettings,
  fetchProjects,
  fetchState,
  updateHubSettings,
  updateProject
} from "../lib/api";

export function useProjects() {
  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [settings, setSettings] = useState<{ poll_interval_seconds: number; auto_sync_enabled: boolean } | null>(null);

  async function refresh() {
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
    refresh();
    const source = new EventSource("/api/events");
    source.addEventListener("change", () => {
      refresh();
    });
    return () => source.close();
  }, []);

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
    }
  ) {
    await updateProject(projectId, input);
    await refresh();
  }

  return { projects, settings, loading, error, refresh, addProject, saveSettings, saveProject };
}

export function useSyncData(projectId: string | null, filters: { team: string; type: string }) {
  const [state, setState] = useState<ProjectState | null>(null);
  const [changes, setChanges] = useState<Change[]>([]);
  const [loading, setLoading] = useState(Boolean(projectId));
  const [error, setError] = useState<string | null>(null);

  async function refresh() {
    if (!projectId) {
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
    setLoading(Boolean(projectId));
    refresh();
  }, [projectId, filters.team, filters.type]);

  useEffect(() => {
    if (!projectId) return;
    const source = new EventSource("/api/events");
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
  }, [projectId, filters.team, filters.type]);

  const changeTypes = useMemo(
    () => Array.from(new Set([...(state?.recent_changes ?? []), ...changes].map((change) => change.type))).sort(),
    [state, changes]
  );

  return { state, changes, changeTypes, loading, error, refresh };
}
