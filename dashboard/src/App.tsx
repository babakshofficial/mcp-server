import { Activity, Boxes, FileText, RefreshCw, Server } from "lucide-react";
import type { ReactNode } from "react";
import { useState } from "react";

import { Badge } from "./components/ui/badge";
import { Button } from "./components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "./components/ui/card";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "./components/ui/tabs";
import { useSyncData } from "./hooks/useSyncData";
import { Change } from "./lib/api";

export default function App() {
  const [tab, setTab] = useState("api");
  const [team, setTeam] = useState("all");
  const [type, setType] = useState("all");
  const { state, changes, changeTypes, loading, error, refresh } = useSyncData({ team, type });

  return (
    <main className="min-h-screen p-6 md:p-10">
      <div className="mx-auto max-w-7xl space-y-6">
        <header className="flex flex-col gap-4 md:flex-row md:items-end md:justify-between">
          <div>
            <p className="text-sm font-medium text-muted-foreground">Team Sync MCP</p>
            <h1 className="mt-2 text-3xl font-bold tracking-tight md:text-4xl">{state?.project ?? "Loading project"}</h1>
            <p className="mt-2 max-w-2xl text-muted-foreground">
              Shared API contracts, component specs, requirements, and team activity from Cursor.
            </p>
          </div>
          <div className="flex items-center gap-2">
            <Badge className="bg-background">Version {state?.version ?? 0}</Badge>
            <Button onClick={refresh} disabled={loading} className="gap-2">
              <RefreshCw className="h-4 w-4" />
              Refresh
            </Button>
          </div>
        </header>

        {error ? <div className="rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-700">{error}</div> : null}

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
                            <td className="p-3"><TeamBadge team={endpoint.team} /></td>
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
              <div className="flex items-start justify-between gap-3">
                <div>
                  <CardTitle className="flex items-center gap-2">
                    <Activity className="h-5 w-5" />
                    Activity feed
                  </CardTitle>
                  <CardDescription>{state?.recent_digest ?? "Waiting for updates."}</CardDescription>
                </div>
              </div>
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
