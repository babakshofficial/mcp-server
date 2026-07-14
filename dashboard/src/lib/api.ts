export type Team = "frontend" | "backend" | "other";

export type Change = {
  id: string;
  project_id?: string;
  version: number;
  timestamp: string;
  team: Team;
  type: string;
  description: string;
  details: Record<string, unknown>;
};

export type ApiEndpoint = {
  method: string;
  path: string;
  description: string;
  team: Team;
  updated_at: string;
};

export type Requirement = {
  id: string;
  title: string;
  description: string;
  status: string;
  team: Team;
  updated_at: string;
};

export type ComponentSpec = {
  name: string;
  spec: string;
  team: Team;
  updated_at: string;
};

export type SubprojectRecord = {
  team: Team;
  status: "pending" | "ready";
  summary: string;
  onboarded_at: string | null;
};

export type ProjectState = {
  project_id: string;
  project: string;
  version: number;
  updated_at: string;
  api: ApiEndpoint[];
  requirements: Requirement[];
  components: ComponentSpec[];
  recent_changes: Change[];
  recent_digest: string;
  subprojects: SubprojectRecord[];
};

export type SyncMode = "interval" | "on_commit";

export type ProjectSummary = {
  id: string;
  name: string;
  description: string;
  version: number;
  updated_at: string;
  open_requirements: number;
  api_count: number;
  component_count: number;
  subprojects: SubprojectRecord[];
  recent_digest: string;
  openapi_url: string;
  auto_sync: boolean;
  sync_mode: SyncMode;
  git_repo_path: string;
  last_git_sha: string;
  last_sync_at: string | null;
  last_sync_status: string;
  last_sync_error: string;
};

export type HubSettings = {
  poll_interval_seconds: number;
  auto_sync_enabled: boolean;
};

function authHeaders(): HeadersInit {
  const token = localStorage.getItem("sync_mcp_token");
  return token ? { Authorization: `Bearer ${token}` } : {};
}

export async function fetchProjects(): Promise<ProjectSummary[]> {
  const response = await fetch("/api/projects");
  if (!response.ok) throw new Error("Failed to load projects");
  return response.json();
}

export async function createProject(input: {
  name: string;
  description?: string;
  openapi_url?: string;
  auto_sync?: boolean;
}): Promise<ProjectSummary> {
  const response = await fetch("/api/projects", {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify(input)
  });
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(detail || "Failed to create project");
  }
  return response.json();
}

export async function updateProject(
  projectId: string,
  input: {
    openapi_url?: string;
    auto_sync?: boolean;
    sync_mode?: SyncMode;
    git_repo_path?: string;
    name?: string;
    description?: string;
  }
): Promise<ProjectSummary> {
  const response = await fetch(`/api/projects/${projectId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify(input)
  });
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(detail || "Failed to update project");
  }
  return response.json();
}

export async function syncProjectNow(projectId: string): Promise<{ changed: boolean; project: ProjectSummary }> {
  const response = await fetch(`/api/projects/${projectId}/sync`, {
    method: "POST",
    headers: { ...authHeaders() }
  });
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(detail || "Failed to sync project");
  }
  return response.json();
}

export async function fetchHubSettings(): Promise<HubSettings> {
  const response = await fetch("/api/settings");
  if (!response.ok) throw new Error("Failed to load settings");
  return response.json();
}

export async function updateHubSettings(input: Partial<HubSettings>): Promise<HubSettings> {
  const response = await fetch("/api/settings", {
    method: "PUT",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify(input)
  });
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(detail || "Failed to update settings");
  }
  return response.json();
}

export async function fetchState(projectId: string): Promise<ProjectState> {
  const response = await fetch(`/api/projects/${projectId}/state`);
  if (!response.ok) throw new Error("Failed to load project state");
  return response.json();
}

export async function fetchChangelog(
  projectId: string,
  filters: { team?: string; type?: string } = {}
): Promise<Change[]> {
  const params = new URLSearchParams({ limit: "100" });
  if (filters.team && filters.team !== "all") params.set("team", filters.team);
  if (filters.type && filters.type !== "all") params.set("type", filters.type);
  const response = await fetch(`/api/projects/${projectId}/changelog?${params.toString()}`);
  if (!response.ok) throw new Error("Failed to load changelog");
  return response.json();
}
