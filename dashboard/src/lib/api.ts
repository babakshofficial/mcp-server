export type Team = "frontend" | "backend" | "other";
export type HubRole = "admin" | "member";
export type ProjectRole = "owner" | "editor" | "viewer";
export type SyncMode = "interval" | "on_commit";

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

export type UserPublic = {
  id: string;
  username: string;
  hub_role: HubRole;
  disabled: boolean;
  created_at: string;
};

export type ProjectMember = {
  project_id: string;
  user_id: string;
  username: string;
  role: ProjectRole;
  created_at: string;
};

export type ApiKeyRecord = {
  id: string;
  user_id: string;
  name: string;
  prefix: string;
  created_at: string;
  last_used_at: string | null;
  revoked_at: string | null;
};

const TOKEN_KEY = "sync_mcp_token";
const USER_KEY = "sync_mcp_user";

export function getStoredToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}

export function getStoredUser(): UserPublic | null {
  const raw = localStorage.getItem(USER_KEY);
  if (!raw) return null;
  try {
    return JSON.parse(raw) as UserPublic;
  } catch {
    return null;
  }
}

export function setSession(token: string, user: UserPublic) {
  localStorage.setItem(TOKEN_KEY, token);
  localStorage.setItem(USER_KEY, JSON.stringify(user));
}

export function clearSession() {
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(USER_KEY);
}

function authHeaders(): HeadersInit {
  const token = getStoredToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

async function parseError(response: Response, fallback: string): Promise<string> {
  try {
    const data = await response.json();
    if (typeof data?.detail === "string") return data.detail;
    return JSON.stringify(data) || fallback;
  } catch {
    return (await response.text()) || fallback;
  }
}

export async function login(username: string, password: string): Promise<{ token: string; user: UserPublic }> {
  const response = await fetch("/api/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password })
  });
  if (!response.ok) throw new Error(await parseError(response, "Login failed"));
  return response.json();
}

export async function fetchMe(): Promise<UserPublic> {
  const response = await fetch("/api/auth/me", { headers: { ...authHeaders() } });
  if (!response.ok) throw new Error(await parseError(response, "Not authenticated"));
  return response.json();
}

export async function fetchProjects(): Promise<ProjectSummary[]> {
  const response = await fetch("/api/projects", { headers: { ...authHeaders() } });
  if (!response.ok) throw new Error(await parseError(response, "Failed to load projects"));
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
  if (!response.ok) throw new Error(await parseError(response, "Failed to create project"));
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
  if (!response.ok) throw new Error(await parseError(response, "Failed to update project"));
  return response.json();
}

export async function deleteProject(projectId: string): Promise<void> {
  const response = await fetch(`/api/projects/${projectId}`, {
    method: "DELETE",
    headers: { ...authHeaders() }
  });
  if (!response.ok) throw new Error(await parseError(response, "Failed to delete project"));
}

export async function syncProjectNow(projectId: string): Promise<{ changed: boolean; project: ProjectSummary }> {
  const response = await fetch(`/api/projects/${projectId}/sync`, {
    method: "POST",
    headers: { ...authHeaders() }
  });
  if (!response.ok) throw new Error(await parseError(response, "Failed to sync project"));
  return response.json();
}

export async function fetchHubSettings(): Promise<HubSettings> {
  const response = await fetch("/api/settings", { headers: { ...authHeaders() } });
  if (!response.ok) throw new Error(await parseError(response, "Failed to load settings"));
  return response.json();
}

export async function updateHubSettings(input: Partial<HubSettings>): Promise<HubSettings> {
  const response = await fetch("/api/settings", {
    method: "PUT",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify(input)
  });
  if (!response.ok) throw new Error(await parseError(response, "Failed to update settings"));
  return response.json();
}

export async function fetchState(projectId: string): Promise<ProjectState> {
  const response = await fetch(`/api/projects/${projectId}/state`, { headers: { ...authHeaders() } });
  if (!response.ok) throw new Error(await parseError(response, "Failed to load project state"));
  return response.json();
}

export async function fetchChangelog(
  projectId: string,
  filters: { team?: string; type?: string } = {}
): Promise<Change[]> {
  const params = new URLSearchParams({ limit: "100" });
  if (filters.team && filters.team !== "all") params.set("team", filters.team);
  if (filters.type && filters.type !== "all") params.set("type", filters.type);
  const response = await fetch(`/api/projects/${projectId}/changelog?${params.toString()}`, {
    headers: { ...authHeaders() }
  });
  if (!response.ok) throw new Error(await parseError(response, "Failed to load changelog"));
  return response.json();
}

export async function fetchMembers(projectId: string): Promise<ProjectMember[]> {
  const response = await fetch(`/api/projects/${projectId}/members`, { headers: { ...authHeaders() } });
  if (!response.ok) throw new Error(await parseError(response, "Failed to load members"));
  return response.json();
}

export async function upsertMember(
  projectId: string,
  input: { username?: string; user_id?: string; role: ProjectRole }
): Promise<ProjectMember> {
  const response = await fetch(`/api/projects/${projectId}/members`, {
    method: "PUT",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify(input)
  });
  if (!response.ok) throw new Error(await parseError(response, "Failed to update member"));
  return response.json();
}

export async function removeMember(projectId: string, userId: string): Promise<void> {
  const response = await fetch(`/api/projects/${projectId}/members/${userId}`, {
    method: "DELETE",
    headers: { ...authHeaders() }
  });
  if (!response.ok) throw new Error(await parseError(response, "Failed to remove member"));
}

export async function fetchUsers(): Promise<UserPublic[]> {
  const response = await fetch("/api/users", { headers: { ...authHeaders() } });
  if (!response.ok) throw new Error(await parseError(response, "Failed to load users"));
  return response.json();
}

export async function createUser(input: {
  username: string;
  password: string;
  hub_role?: HubRole;
}): Promise<UserPublic> {
  const response = await fetch("/api/users", {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify(input)
  });
  if (!response.ok) throw new Error(await parseError(response, "Failed to create user"));
  return response.json();
}

export async function updateUser(
  userId: string,
  input: { hub_role?: HubRole; disabled?: boolean; password?: string }
): Promise<UserPublic> {
  const response = await fetch(`/api/users/${userId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify(input)
  });
  if (!response.ok) throw new Error(await parseError(response, "Failed to update user"));
  return response.json();
}

export async function fetchApiKeys(): Promise<ApiKeyRecord[]> {
  const response = await fetch("/api/api-keys", { headers: { ...authHeaders() } });
  if (!response.ok) throw new Error(await parseError(response, "Failed to load API keys"));
  return response.json();
}

export async function createApiKey(name: string): Promise<{ key: ApiKeyRecord; raw_key: string }> {
  const response = await fetch("/api/api-keys", {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify({ name })
  });
  if (!response.ok) throw new Error(await parseError(response, "Failed to create API key"));
  return response.json();
}

export async function revokeApiKey(keyId: string): Promise<void> {
  const response = await fetch(`/api/api-keys/${keyId}`, {
    method: "DELETE",
    headers: { ...authHeaders() }
  });
  if (!response.ok) throw new Error(await parseError(response, "Failed to revoke API key"));
}

export function eventsUrl(): string {
  const token = getStoredToken();
  return token ? `/api/events?access_token=${encodeURIComponent(token)}` : "/api/events";
}
