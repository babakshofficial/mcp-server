import { Activity, ArrowLeft, Boxes, FileText, FolderKanban, Plus, RefreshCw, Server } from "lucide-react";
import type { FormEvent, ReactNode } from "react";
import { useEffect, useMemo, useState } from "react";

import { Badge } from "./components/ui/badge";
import { Button } from "./components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "./components/ui/card";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "./components/ui/tabs";
import { useAuth, useProjects, useSyncData } from "./hooks/useSyncData";
import {
  Change,
  ProjectMember,
  ProjectRole,
  ProjectSummary,
  SubprojectRecord,
  UserPublic,
  createApiKey,
  createUser,
  deleteProject,
  fetchApiKeys,
  fetchMembers,
  fetchUsers,
  login,
  removeMember,
  revokeApiKey,
  setSession,
  syncProjectNow,
  updateUser,
  upsertMember
} from "./lib/api";

function selectedProjectFromUrl(): string | null {
  const params = new URLSearchParams(window.location.search);
  return params.get("project");
}

type View = "projects" | "admin" | "keys";

export default function App() {
  const auth = useAuth();
  const [projectId, setProjectId] = useState<string | null>(selectedProjectFromUrl());
  const [view, setView] = useState<View>("projects");
  const projectsApi = useProjects(Boolean(auth.user));

  if (auth.loading) {
    return (
      <main className="flex min-h-screen items-center justify-center p-6">
        <p className="text-muted-foreground">Loading…</p>
      </main>
    );
  }

  if (!auth.user) {
    return <LoginScreen onLoggedIn={(user, token) => {
      setSession(token, user);
      auth.setUser(user);
    }} />;
  }

  function openProject(id: string) {
    const params = new URLSearchParams(window.location.search);
    params.set("project", id);
    window.history.replaceState({}, "", `${window.location.pathname}?${params.toString()}`);
    setProjectId(id);
    setView("projects");
  }

  function backToList() {
    window.history.replaceState({}, "", window.location.pathname);
    setProjectId(null);
    projectsApi.refresh();
  }

  if (projectId && view === "projects") {
    return (
      <ProjectDetail
        projectId={projectId}
        onBack={backToList}
        projectsApi={projectsApi}
        user={auth.user}
        onLogout={auth.logout}
        isAdmin={auth.isAdmin}
      />
    );
  }

  return (
    <Shell user={auth.user} isAdmin={auth.isAdmin} onLogout={auth.logout} view={view} setView={setView}>
      {view === "admin" && auth.isAdmin ? <AdminUsers /> : null}
      {view === "keys" ? <ApiKeysPanel /> : null}
      {view === "projects" ? <ProjectsOverview api={projectsApi} onOpen={openProject} isAdmin={auth.isAdmin} /> : null}
    </Shell>
  );
}

function Shell({
  user,
  isAdmin,
  onLogout,
  view,
  setView,
  children
}: {
  user: UserPublic;
  isAdmin: boolean;
  onLogout: () => void;
  view: View;
  setView: (v: View) => void;
  children: ReactNode;
}) {
  return (
    <main className="min-h-screen p-6 md:p-10">
      <div className="mx-auto max-w-7xl space-y-6">
        <header className="flex flex-col gap-4 md:flex-row md:items-end md:justify-between">
          <div>
            <p className="text-sm font-medium text-muted-foreground">Team Sync MCP</p>
            <h1 className="mt-2 text-3xl font-bold tracking-tight md:text-4xl">
              {view === "admin" ? "Users" : view === "keys" ? "API keys" : "Projects"}
            </h1>
            <p className="mt-2 text-sm text-muted-foreground">
              Signed in as <span className="font-medium text-foreground">{user.username}</span>
              {isAdmin ? " (admin)" : ""}
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            <Button variant="outline" onClick={() => setView("projects")}>
              Projects
            </Button>
            <Button variant="outline" onClick={() => setView("keys")}>
              API keys
            </Button>
            {isAdmin ? (
              <Button variant="outline" onClick={() => setView("admin")}>
                Admin
              </Button>
            ) : null}
            <Button variant="outline" onClick={onLogout}>
              Log out
            </Button>
          </div>
        </header>
        {children}
      </div>
    </main>
  );
}

function LoginScreen({ onLoggedIn }: { onLoggedIn: (user: UserPublic, token: string) => void }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const result = await login(username.trim(), password);
      onLoggedIn(result.user, result.token);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Login failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="flex min-h-screen items-center justify-center p-6">
      <Card className="w-full max-w-md">
        <CardHeader>
          <CardTitle>Sign in</CardTitle>
          <CardDescription>Use your hub username and password.</CardDescription>
        </CardHeader>
        <CardContent>
          <form className="grid gap-3" onSubmit={handleSubmit}>
            <input
              className="h-10 rounded-md border bg-background px-3 text-sm"
              placeholder="Username"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              autoComplete="username"
            />
            <input
              className="h-10 rounded-md border bg-background px-3 text-sm"
              placeholder="Password"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete="current-password"
            />
            <Button type="submit" disabled={busy || !username.trim() || !password}>
              {busy ? "Signing in…" : "Sign in"}
            </Button>
          </form>
          {error ? <p className="mt-3 text-sm text-red-600">{error}</p> : null}
        </CardContent>
      </Card>
    </main>
  );
}

function ProjectsOverview({
  api,
  onOpen,
  isAdmin
}: {
  api: ReturnType<typeof useProjects>;
  onOpen: (id: string) => void;
  isAdmin: boolean;
}) {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [openapiUrl, setOpenapiUrl] = useState("");
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);
  const [intervalSeconds, setIntervalSeconds] = useState("30");
  const [autoEnabled, setAutoEnabled] = useState(true);
  const [settingsError, setSettingsError] = useState<string | null>(null);

  useEffect(() => {
    if (api.settings) {
      setIntervalSeconds(String(api.settings.poll_interval_seconds));
      setAutoEnabled(api.settings.auto_sync_enabled);
    }
  }, [api.settings]);

  async function handleCreate(event: FormEvent) {
    event.preventDefault();
    if (!name.trim()) return;
    setCreating(true);
    setCreateError(null);
    try {
      const created = await api.addProject({
        name: name.trim(),
        description: description.trim(),
        openapi_url: openapiUrl.trim(),
        auto_sync: true
      });
      setName("");
      setDescription("");
      setOpenapiUrl("");
      onOpen(created.id);
    } catch (err) {
      setCreateError(err instanceof Error ? err.message : "Create failed");
    } finally {
      setCreating(false);
    }
  }

  async function handleSaveSettings(event: FormEvent) {
    event.preventDefault();
    setSettingsError(null);
    try {
      await api.saveSettings({
        poll_interval_seconds: Number(intervalSeconds) || 30,
        auto_sync_enabled: autoEnabled
      });
    } catch (err) {
      setSettingsError(err instanceof Error ? err.message : "Failed to save settings");
    }
  }

  return (
    <>
      {api.error ? <div className="rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-700">{api.error}</div> : null}

      {isAdmin ? (
        <Card>
          <CardHeader>
            <CardTitle>Auto-sync settings</CardTitle>
            <CardDescription>Hub-wide poll interval for all projects with an OpenAPI URL.</CardDescription>
          </CardHeader>
          <CardContent>
            <form className="grid gap-3 md:grid-cols-3" onSubmit={handleSaveSettings}>
              <label className="grid gap-1 text-xs font-medium text-muted-foreground">
                Poll interval (seconds)
                <input
                  className="h-10 rounded-md border bg-background px-3 text-sm text-foreground"
                  type="number"
                  min={5}
                  max={3600}
                  value={intervalSeconds}
                  onChange={(event) => setIntervalSeconds(event.target.value)}
                />
              </label>
              <label className="flex items-center gap-2 self-end pb-2 text-sm">
                <input type="checkbox" checked={autoEnabled} onChange={(event) => setAutoEnabled(event.target.checked)} />
                Enable auto-sync
              </label>
              <Button type="submit">Save settings</Button>
            </form>
            {settingsError ? <p className="mt-3 text-sm text-sm text-red-600">{settingsError}</p> : null}
          </CardContent>
        </Card>
      ) : null}

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Plus className="h-5 w-5" />
            Create project
          </CardTitle>
          <CardDescription>You become the project owner. Set an OpenAPI URL to enable auto-sync.</CardDescription>
        </CardHeader>
        <CardContent>
          <form className="grid gap-3 md:grid-cols-2" onSubmit={handleCreate}>
            <input
              className="h-10 rounded-md border bg-background px-3 text-sm"
              placeholder="Project name"
              value={name}
              onChange={(event) => setName(event.target.value)}
            />
            <input
              className="h-10 rounded-md border bg-background px-3 text-sm"
              placeholder="Description"
              value={description}
              onChange={(event) => setDescription(event.target.value)}
            />
            <input
              className="h-10 rounded-md border bg-background px-3 text-sm md:col-span-2"
              placeholder="OpenAPI URL (optional)"
              value={openapiUrl}
              onChange={(event) => setOpenapiUrl(event.target.value)}
            />
            <Button type="submit" disabled={creating || !name.trim()} className="md:col-span-2">
              {creating ? "Creating..." : "Create"}
            </Button>
          </form>
          {createError ? <p className="mt-3 text-sm text-red-600">{createError}</p> : null}
        </CardContent>
      </Card>

      <section className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        {api.projects.map((project) => (
          <ProjectCard key={project.id} project={project} onOpen={() => onOpen(project.id)} />
        ))}
        {!api.projects.length && !api.loading ? (
          <EmptyState label="No projects yet. Create one or ask an admin to add you as a member." />
        ) : null}
      </section>
    </>
  );
}

function ProjectCard({ project, onOpen }: { project: ProjectSummary; onOpen: () => void }) {
  return (
    <Card className="cursor-pointer transition hover:border-primary/40" onClick={onOpen}>
      <CardHeader>
        <div className="flex items-start justify-between gap-3">
          <div>
            <CardTitle className="flex items-center gap-2">
              <FolderKanban className="h-5 w-5 text-primary" />
              {project.name}
            </CardTitle>
            <CardDescription className="mt-2">{project.description || project.id}</CardDescription>
          </div>
          <Badge>v{project.version}</Badge>
        </div>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="flex flex-wrap gap-2">
          <Badge className="bg-muted">API {project.api_count}</Badge>
          <Badge className="bg-muted">Reqs {project.open_requirements}</Badge>
          <Badge className="bg-muted">UI {project.component_count}</Badge>
        </div>
        <SubprojectBadges subprojects={project.subprojects} />
        <p className="text-xs text-muted-foreground">{new Date(project.updated_at).toLocaleString()}</p>
      </CardContent>
    </Card>
  );
}

function ProjectDetail({
  projectId,
  onBack,
  projectsApi,
  user,
  onLogout,
  isAdmin
}: {
  projectId: string;
  onBack: () => void;
  projectsApi: ReturnType<typeof useProjects>;
  user: UserPublic;
  onLogout: () => void;
  isAdmin: boolean;
}) {
  const [tab, setTab] = useState("api");
  const [team, setTeam] = useState("all");
  const [type, setType] = useState("all");
  const { state, changes, changeTypes, loading, error, refresh } = useSyncData(projectId, { team, type }, true);
  const projectMeta = projectsApi.projects.find((item) => item.id === projectId);
  const [projectName, setProjectName] = useState(projectMeta?.name || "");
  const [projectDescription, setProjectDescription] = useState(projectMeta?.description || "");
  const [openapiUrl, setOpenapiUrl] = useState(projectMeta?.openapi_url || "");
  const [autoSync, setAutoSync] = useState(projectMeta?.auto_sync ?? true);
  const [syncMode, setSyncMode] = useState<"interval" | "on_commit">(projectMeta?.sync_mode || "interval");
  const [gitRepoPath, setGitRepoPath] = useState(projectMeta?.git_repo_path || "");
  const [syncMessage, setSyncMessage] = useState<string | null>(null);
  const [syncError, setSyncError] = useState<string | null>(null);
  const [members, setMembers] = useState<ProjectMember[]>([]);
  const [memberUsername, setMemberUsername] = useState("");
  const [memberRole, setMemberRole] = useState<ProjectRole>("editor");
  const [memberError, setMemberError] = useState<string | null>(null);

  useEffect(() => {
    if (projectMeta) {
      setProjectName(projectMeta.name || "");
      setProjectDescription(projectMeta.description || "");
      setOpenapiUrl(projectMeta.openapi_url || "");
      setAutoSync(projectMeta.auto_sync);
      setSyncMode(projectMeta.sync_mode || "interval");
      setGitRepoPath(projectMeta.git_repo_path || "");
    }
  }, [projectMeta]);

  useEffect(() => {
    fetchMembers(projectId)
      .then(setMembers)
      .catch(() => setMembers([]));
  }, [projectId]);

  const title = useMemo(() => state?.project ?? projectId, [state, projectId]);
  const myRole = members.find((m) => m.user_id === user.id)?.role;
  const canOwn = isAdmin || myRole === "owner";
  const canEdit = canOwn || myRole === "editor";

  async function saveAutoSync(event: FormEvent) {
    event.preventDefault();
    setSyncError(null);
    try {
      await projectsApi.saveProject(projectId, {
        name: projectName.trim() || undefined,
        description: projectDescription,
        openapi_url: openapiUrl.trim(),
        auto_sync: autoSync,
        sync_mode: syncMode,
        git_repo_path: gitRepoPath.trim()
      });
      setSyncMessage("Project settings saved.");
    } catch (err) {
      setSyncError(err instanceof Error ? err.message : "Failed to save");
    }
  }

  async function runSyncNow() {
    setSyncError(null);
    try {
      const result = await syncProjectNow(projectId);
      setSyncMessage(result.changed ? "Synced — API surface updated." : "Synced — no OpenAPI changes.");
      await refresh();
      await projectsApi.refresh();
    } catch (err) {
      setSyncError(err instanceof Error ? err.message : "Sync failed");
    }
  }

  async function handleDelete() {
    if (!window.confirm(`Delete project "${projectId}"? This cannot be undone.`)) return;
    try {
      await deleteProject(projectId);
      onBack();
    } catch (err) {
      setSyncError(err instanceof Error ? err.message : "Delete failed");
    }
  }

  async function handleAddMember(event: FormEvent) {
    event.preventDefault();
    setMemberError(null);
    try {
      await upsertMember(projectId, { username: memberUsername.trim(), role: memberRole });
      setMemberUsername("");
      setMembers(await fetchMembers(projectId));
    } catch (err) {
      setMemberError(err instanceof Error ? err.message : "Failed to add member");
    }
  }

  return (
    <main className="min-h-screen p-6 md:p-10">
      <div className="mx-auto max-w-7xl space-y-6">
        <header className="flex flex-col gap-4 md:flex-row md:items-end md:justify-between">
          <div>
            <button className="mb-3 inline-flex items-center gap-2 text-sm text-muted-foreground hover:text-foreground" onClick={onBack}>
              <ArrowLeft className="h-4 w-4" />
              All projects
            </button>
            <p className="text-sm font-medium text-muted-foreground">
              {user.username}
              {myRole ? ` · ${myRole}` : isAdmin ? " · admin" : ""}
            </p>
            <h1 className="mt-2 text-3xl font-bold tracking-tight md:text-4xl">{title}</h1>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Badge className="bg-background">Version {state?.version ?? 0}</Badge>
            <Button onClick={refresh} disabled={loading} className="gap-2">
              <RefreshCw className="h-4 w-4" />
              Refresh
            </Button>
            <Button variant="outline" onClick={onLogout}>
              Log out
            </Button>
          </div>
        </header>

        {error ? <div className="rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-700">{error}</div> : null}

        {canOwn ? (
          <Card>
            <CardHeader>
              <CardTitle>Project settings</CardTitle>
              <CardDescription>Edit metadata and OpenAPI sync. Owners and hub admins only.</CardDescription>
            </CardHeader>
            <CardContent>
              <form className="grid gap-3" onSubmit={saveAutoSync}>
                <input
                  className="h-10 rounded-md border bg-background px-3 text-sm"
                  placeholder="Name"
                  value={projectName}
                  onChange={(e) => setProjectName(e.target.value)}
                />
                <input
                  className="h-10 rounded-md border bg-background px-3 text-sm"
                  placeholder="Description"
                  value={projectDescription}
                  onChange={(e) => setProjectDescription(e.target.value)}
                />
                <input
                  className="h-10 rounded-md border bg-background px-3 text-sm"
                  placeholder="http://host:8001/openapi.json"
                  value={openapiUrl}
                  onChange={(event) => setOpenapiUrl(event.target.value)}
                />
                <label className="grid gap-1 text-sm">
                  <span className="text-muted-foreground">Sync mode</span>
                  <select
                    className="h-10 rounded-md border bg-background px-3 text-sm"
                    value={syncMode}
                    onChange={(event) => setSyncMode(event.target.value as "interval" | "on_commit")}
                  >
                    <option value="interval">Every N seconds</option>
                    <option value="on_commit">After each commit (local git)</option>
                  </select>
                </label>
                {syncMode === "on_commit" ? (
                  <input
                    className="h-10 rounded-md border bg-background px-3 text-sm"
                    placeholder="/absolute/path/to/backend-repo"
                    value={gitRepoPath}
                    onChange={(event) => setGitRepoPath(event.target.value)}
                  />
                ) : null}
                <label className="flex items-center gap-2 text-sm">
                  <input type="checkbox" checked={autoSync} onChange={(event) => setAutoSync(event.target.checked)} />
                  Enable auto-sync for this project
                </label>
                <div className="flex flex-wrap gap-2">
                  <Button type="submit">Save</Button>
                  {canEdit ? (
                    <Button type="button" onClick={runSyncNow} disabled={!openapiUrl.trim()}>
                      Sync now
                    </Button>
                  ) : null}
                  <Button type="button" variant="outline" onClick={handleDelete}>
                    Delete project
                  </Button>
                </div>
              </form>
              {projectMeta?.last_sync_at ? (
                <p className="mt-3 text-xs text-muted-foreground">
                  Last sync: {new Date(projectMeta.last_sync_at).toLocaleString()} ({projectMeta.last_sync_status || "n/a"})
                </p>
              ) : null}
              {syncMessage ? <p className="mt-2 text-sm text-emerald-700">{syncMessage}</p> : null}
              {syncError ? <p className="mt-2 text-sm text-red-600">{syncError}</p> : null}
            </CardContent>
          </Card>
        ) : canEdit ? (
          <Card>
            <CardHeader>
              <CardTitle>Sync</CardTitle>
            </CardHeader>
            <CardContent>
              <Button onClick={runSyncNow} disabled={!openapiUrl.trim()}>
                Sync now
              </Button>
              {syncMessage ? <p className="mt-2 text-sm text-emerald-700">{syncMessage}</p> : null}
              {syncError ? <p className="mt-2 text-sm text-red-600">{syncError}</p> : null}
            </CardContent>
          </Card>
        ) : null}

        {canOwn ? (
          <Card>
            <CardHeader>
              <CardTitle>Members</CardTitle>
              <CardDescription>Assign owner / editor / viewer roles for this project.</CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              <form className="grid gap-3 md:grid-cols-3" onSubmit={handleAddMember}>
                <input
                  className="h-10 rounded-md border bg-background px-3 text-sm"
                  placeholder="Username"
                  value={memberUsername}
                  onChange={(e) => setMemberUsername(e.target.value)}
                />
                <select
                  className="h-10 rounded-md border bg-background px-3 text-sm"
                  value={memberRole}
                  onChange={(e) => setMemberRole(e.target.value as ProjectRole)}
                >
                  <option value="owner">owner</option>
                  <option value="editor">editor</option>
                  <option value="viewer">viewer</option>
                </select>
                <Button type="submit" disabled={!memberUsername.trim()}>
                  Add / update
                </Button>
              </form>
              {memberError ? <p className="text-sm text-red-600">{memberError}</p> : null}
              <div className="space-y-2">
                {members.map((m) => (
                  <div key={m.user_id} className="flex items-center justify-between rounded-md border px-3 py-2 text-sm">
                    <span>
                      {m.username || m.user_id} · {m.role}
                    </span>
                    <Button
                      variant="outline"
                      onClick={async () => {
                        await removeMember(projectId, m.user_id);
                        setMembers(await fetchMembers(projectId));
                      }}
                    >
                      Remove
                    </Button>
                  </div>
                ))}
              </div>
            </CardContent>
          </Card>
        ) : null}

        <Card>
          <CardHeader>
            <CardTitle>Subproject status</CardTitle>
          </CardHeader>
          <CardContent>
            <SubprojectBadges subprojects={state?.subprojects ?? []} />
          </CardContent>
        </Card>

        <section className="grid gap-4 md:grid-cols-3">
          <SummaryCard icon={<Server />} title="API endpoints" value={state?.api.length ?? 0} />
          <SummaryCard
            icon={<FileText />}
            title="Open requirements"
            value={state?.requirements.filter((item) => item.status === "open").length ?? 0}
          />
          <SummaryCard icon={<Boxes />} title="Components" value={state?.components.length ?? 0} />
        </section>

        <section className="grid gap-6 lg:grid-cols-[1fr_420px]">
          <Card>
            <CardHeader>
              <CardTitle>Latest shared state</CardTitle>
              <CardDescription>Updated {state ? new Date(state.updated_at).toLocaleString() : "never"}</CardDescription>
            </CardHeader>
            <CardContent>
              <Tabs value={tab} onValueChange={setTab}>
                <TabsList>
                  <TabsTrigger value="api">API</TabsTrigger>
                  <TabsTrigger value="requirements">Requirements</TabsTrigger>
                  <TabsTrigger value="components">Components</TabsTrigger>
                </TabsList>
                <TabsContent value="api">
                  <div className="overflow-hidden rounded-lg border">
                    <table className="w-full text-sm">
                      <thead className="bg-muted text-left">
                        <tr>
                          <th className="p-3">Method</th>
                          <th className="p-3">Path</th>
                          <th className="p-3">Description</th>
                          <th className="p-3">Team</th>
                        </tr>
                      </thead>
                      <tbody>
                        {state?.api.map((endpoint) => (
                          <tr key={`${endpoint.method}-${endpoint.path}`} className="border-t">
                            <td className="p-3 font-mono text-xs">{endpoint.method}</td>
                            <td className="p-3 font-mono">{endpoint.path}</td>
                            <td className="p-3 text-muted-foreground">{endpoint.description}</td>
                            <td className="p-3">
                              <TeamBadge team={endpoint.team} />
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                    {!state?.api.length ? <EmptyState label="No API endpoints published yet." /> : null}
                  </div>
                </TabsContent>
                <TabsContent value="requirements">
                  <div className="grid gap-3">
                    {state?.requirements.map((requirement) => (
                      <div key={requirement.id} className="rounded-lg border p-4">
                        <div className="flex flex-wrap items-center gap-2">
                          <h3 className="font-semibold">{requirement.title}</h3>
                          <Badge>{requirement.status}</Badge>
                          <TeamBadge team={requirement.team} />
                        </div>
                        <p className="mt-2 text-sm text-muted-foreground">{requirement.description}</p>
                      </div>
                    ))}
                    {!state?.requirements.length ? <EmptyState label="No requirements published yet." /> : null}
                  </div>
                </TabsContent>
                <TabsContent value="components">
                  <div className="grid gap-3 md:grid-cols-2">
                    {state?.components.map((component) => (
                      <div key={component.name} className="rounded-lg border p-4">
                        <div className="flex items-center justify-between gap-2">
                          <h3 className="font-semibold">{component.name}</h3>
                          <TeamBadge team={component.team} />
                        </div>
                        <p className="mt-2 text-sm text-muted-foreground">{component.spec}</p>
                      </div>
                    ))}
                    {!state?.components.length ? <EmptyState label="No component specs published yet." /> : null}
                  </div>
                </TabsContent>
              </Tabs>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <Activity className="h-5 w-5" />
                Activity feed
              </CardTitle>
              <CardDescription>{state?.recent_digest ?? "Waiting for updates."}</CardDescription>
              <div className="grid grid-cols-2 gap-3 pt-3">
                <FilterSelect label="Team" value={team} onChange={setTeam} options={["all", "frontend", "backend", "other"]} />
                <FilterSelect label="Type" value={type} onChange={setType} options={["all", ...changeTypes]} />
              </div>
            </CardHeader>
            <CardContent>
              <div className="max-h-[620px] space-y-3 overflow-y-auto pr-2">
                {changes.map((change) => (
                  <ChangeItem key={change.id} change={change} />
                ))}
                {!changes.length ? <EmptyState label="No matching changes yet." /> : null}
              </div>
            </CardContent>
          </Card>
        </section>
      </div>
    </main>
  );
}

function AdminUsers() {
  const [users, setUsers] = useState<UserPublic[]>([]);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);

  async function refresh() {
    setUsers(await fetchUsers());
  }

  useEffect(() => {
    refresh().catch((err) => setError(err instanceof Error ? err.message : "Failed to load users"));
  }, []);

  async function handleCreate(event: FormEvent) {
    event.preventDefault();
    setError(null);
    try {
      await createUser({ username: username.trim(), password, hub_role: "member" });
      setUsername("");
      setPassword("");
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Create failed");
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Users</CardTitle>
        <CardDescription>Create hub users and change roles.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <form className="grid gap-3 md:grid-cols-3" onSubmit={handleCreate}>
          <input
            className="h-10 rounded-md border bg-background px-3 text-sm"
            placeholder="Username"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
          />
          <input
            className="h-10 rounded-md border bg-background px-3 text-sm"
            placeholder="Password"
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
          <Button type="submit">Create member</Button>
        </form>
        {error ? <p className="text-sm text-red-600">{error}</p> : null}
        <div className="space-y-2">
          {users.map((u) => (
            <div key={u.id} className="flex flex-wrap items-center justify-between gap-2 rounded-md border px-3 py-2 text-sm">
              <span>
                {u.username} · {u.hub_role}
                {u.disabled ? " · disabled" : ""}
              </span>
              <div className="flex gap-2">
                <Button
                  variant="outline"
                  onClick={async () => {
                    await updateUser(u.id, { hub_role: u.hub_role === "admin" ? "member" : "admin" });
                    await refresh();
                  }}
                >
                  Toggle admin
                </Button>
                <Button
                  variant="outline"
                  onClick={async () => {
                    await updateUser(u.id, { disabled: !u.disabled });
                    await refresh();
                  }}
                >
                  {u.disabled ? "Enable" : "Disable"}
                </Button>
              </div>
            </div>
          ))}
        </div>
      </CardContent>
    </Card>
  );
}

function ApiKeysPanel() {
  const [keys, setKeys] = useState<Awaited<ReturnType<typeof fetchApiKeys>>>([]);
  const [name, setName] = useState("cursor");
  const [rawKey, setRawKey] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function refresh() {
    setKeys(await fetchApiKeys());
  }

  useEffect(() => {
    refresh().catch((err) => setError(err instanceof Error ? err.message : "Failed to load keys"));
  }, []);

  return (
    <Card>
      <CardHeader>
        <CardTitle>API keys</CardTitle>
        <CardDescription>
          Use as <code className="text-xs">Authorization: Bearer sk_…</code> with <code className="text-xs">Project: name-backend</code> in Cursor.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <form
          className="flex flex-wrap gap-2"
          onSubmit={async (event) => {
            event.preventDefault();
            setError(null);
            try {
              const created = await createApiKey(name.trim() || "default");
              setRawKey(created.raw_key);
              await refresh();
            } catch (err) {
              setError(err instanceof Error ? err.message : "Failed to create key");
            }
          }}
        >
          <input
            className="h-10 rounded-md border bg-background px-3 text-sm"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Key name"
          />
          <Button type="submit">Create key</Button>
        </form>
        {rawKey ? (
          <div className="rounded-md border border-emerald-200 bg-emerald-50 p-3 text-sm">
            Copy now (shown once): <code className="break-all font-mono text-xs">{rawKey}</code>
          </div>
        ) : null}
        {error ? <p className="text-sm text-red-600">{error}</p> : null}
        <div className="space-y-2">
          {keys.map((key) => (
            <div key={key.id} className="flex items-center justify-between rounded-md border px-3 py-2 text-sm">
              <span>
                {key.name} · {key.prefix}…
                {key.revoked_at ? " · revoked" : ""}
              </span>
              {!key.revoked_at ? (
                <Button
                  variant="outline"
                  onClick={async () => {
                    await revokeApiKey(key.id);
                    await refresh();
                  }}
                >
                  Revoke
                </Button>
              ) : null}
            </div>
          ))}
        </div>
      </CardContent>
    </Card>
  );
}

function SubprojectBadges({ subprojects }: { subprojects: SubprojectRecord[] }) {
  const teams: Array<"backend" | "frontend" | "other"> = ["backend", "frontend", "other"];
  return (
    <div className="flex flex-wrap gap-2">
      {teams.map((team) => {
        const record = subprojects.find((item) => item.team === team);
        const ready = record?.status === "ready";
        return (
          <Badge key={team} className={ready ? "bg-emerald-50 text-emerald-700" : "bg-muted text-muted-foreground"}>
            {team}: {ready ? "ready" : "pending"}
          </Badge>
        );
      })}
    </div>
  );
}

function SummaryCard({ icon, title, value }: { icon: ReactNode; title: string; value: number }) {
  return (
    <Card>
      <CardContent className="flex items-center gap-4 pt-6">
        <div className="rounded-lg bg-muted p-3 text-primary">{icon}</div>
        <div>
          <p className="text-sm text-muted-foreground">{title}</p>
          <p className="text-2xl font-bold">{value}</p>
        </div>
      </CardContent>
    </Card>
  );
}

function ChangeItem({ change }: { change: Change }) {
  return (
    <article className="rounded-lg border p-4">
      <div className="flex flex-wrap items-center gap-2">
        <TeamBadge team={change.team} />
        <Badge>{change.type}</Badge>
        <span className="text-xs text-muted-foreground">v{change.version}</span>
      </div>
      <p className="mt-3 font-medium">{change.description}</p>
      <p className="mt-1 text-xs text-muted-foreground">{new Date(change.timestamp).toLocaleString()}</p>
    </article>
  );
}

function TeamBadge({ team }: { team: string }) {
  return <Badge className="capitalize">{team}</Badge>;
}

function FilterSelect({
  label,
  value,
  onChange,
  options
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  options: string[];
}) {
  return (
    <label className="grid gap-1 text-xs font-medium text-muted-foreground">
      {label}
      <select
        value={value}
        onChange={(event) => onChange(event.target.value)}
        className="h-10 rounded-md border bg-background px-3 text-sm text-foreground"
      >
        {options.map((option) => (
          <option key={option} value={option}>
            {option}
          </option>
        ))}
      </select>
    </label>
  );
}

function EmptyState({ label }: { label: string }) {
  return <div className="p-6 text-center text-sm text-muted-foreground">{label}</div>;
}
