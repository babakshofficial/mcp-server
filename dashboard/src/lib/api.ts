export type Team = "frontend" | "backend" | "other";

export type Change = {
  id: string;
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

export type ProjectState = {
  project: string;
  version: number;
  updated_at: string;
  api: ApiEndpoint[];
  requirements: Requirement[];
  components: ComponentSpec[];
  recent_changes: Change[];
  recent_digest: string;
};

export async function fetchState(): Promise<ProjectState> {
  const response = await fetch("/api/state");
  if (!response.ok) throw new Error("Failed to load project state");
  return response.json();
}

export async function fetchChangelog(filters: { team?: string; type?: string } = {}): Promise<Change[]> {
  const params = new URLSearchParams({ limit: "100" });
  if (filters.team && filters.team !== "all") params.set("team", filters.team);
  if (filters.type && filters.type !== "all") params.set("type", filters.type);
  const response = await fetch(`/api/changelog?${params.toString()}`);
  if (!response.ok) throw new Error("Failed to load changelog");
  return response.json();
}
