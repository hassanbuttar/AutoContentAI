import { useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { FolderGit2, Play, Plus, RefreshCw, Sparkles, Trash2, Upload } from "lucide-react";
import { api } from "@/lib/api";
import { useAction, useApi, useToast } from "@/lib/hooks";
import { CONTENT_TYPES, fmtDate, fmtRelative, label } from "@/lib/utils";
import {
  Badge, Button, Card, CardContent, CardDescription, CardHeader, CardTitle, Dialog, Empty, ErrorBox, Field, Input,
  PageHeader, Select, Spinner, StatusBadge, Switch, Tabs, Td, Table, Th, Textarea,
} from "@/components/ui";
import { AppForm } from "./Applications";

const SOURCE_TYPES = [
  ["local_git", "Local Git repository"], ["local_folder", "Local source folder"], ["github", "GitHub repository"],
  ["gitlab", "GitLab repository"], ["docs_folder", "Documentation folder"], ["changelog", "Changelog file"],
  ["release_notes", "Release notes"], ["spec_doc", "Product specification"], ["manual", "Manual feature notes"],
];

function Sources({ appId }: { appId: number }) {
  const { data } = useApi<any[]>(["sources", appId], `/api/applications/${appId}/sources`);
  const [form, setForm] = useState<any>({ source_type: "local_git", location: "", branch: "", token: "", text: "" });
  const [open, setOpen] = useState(false);
  const add = useAction((v: any) => api.post(`/api/applications/${appId}/sources`, {
    source_type: v.source_type, location: v.location, branch: v.branch || null, token: v.token || null,
    config: v.source_type === "manual" ? { text: v.text } : {},
  }), [["sources", appId], ["application", appId]], "Source added");
  const del = useAction((id: number) => api.del(`/api/sources/${id}`), [["sources", appId]], "Source removed");
  const toggle = useAction((s: any) => api.patch(`/api/sources/${s.id}`, { ...s, enabled: !s.enabled }), [["sources", appId]]);
  const isLocal = !["github", "gitlab", "manual"].includes(form.source_type);
  return (
    <Card>
      <CardHeader>
        <div>
          <CardTitle>Project sources</CardTitle>
          <CardDescription>Read-only. Local paths must be inside an allowed project folder (Settings → Security). Code is never executed.</CardDescription>
        </div>
        <Button size="sm" onClick={() => setOpen(true)}><Plus /> Add source</Button>
      </CardHeader>
      <CardContent>
        {!data?.length ? <Empty icon={FolderGit2} title="No sources">Connect a repository, docs folder, changelog, or write feature notes.</Empty> : (
          <Table>
            <thead><tr><Th>Type</Th><Th>Location</Th><Th>Last scan</Th><Th>Last commit</Th><Th>Enabled</Th><Th /></tr></thead>
            <tbody>
              {data.map((s) => (
                <tr key={s.id}>
                  <Td><Badge>{label(s.source_type)}</Badge></Td>
                  <Td className="max-w-xs">
                    <p className="truncate font-mono text-xs">{s.source_type === "manual" ? `${(s.config?.text ?? "").slice(0, 60)}…` : s.location}</p>
                    {s.last_error && <p className="mt-1 text-xs text-danger">{s.last_error}</p>}
                  </Td>
                  <Td className="text-xs text-muted-foreground">{fmtRelative(s.last_scanned_at)}</Td>
                  <Td className="font-mono text-xs">{s.last_commit?.slice(0, 8) ?? "—"}</Td>
                  <Td><Switch checked={s.enabled} onChange={() => toggle.mutate(s)} /></Td>
                  <Td className="text-right"><Button size="icon" variant="ghost" onClick={() => confirm("Remove source?") && del.mutate(s.id)}><Trash2 /></Button></Td>
                </tr>
              ))}
            </tbody>
          </Table>
        )}
      </CardContent>
      <Dialog open={open} onClose={() => setOpen(false)} title="Add project source">
        <form className="space-y-4" onSubmit={async (e) => {
          e.preventDefault();
          await add.mutateAsync(form);
          setOpen(false);
          setForm({ ...form, location: "", token: "", text: "" });
        }}>
          <Field label="Source type">
            <Select value={form.source_type} onChange={(e) => setForm({ ...form, source_type: e.target.value })}>
              {SOURCE_TYPES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
            </Select>
          </Field>
          {form.source_type === "manual" ? (
            <Field label="Feature notes" hint="One feature per line. Treated as owner-verified information.">
              <Textarea rows={8} value={form.text} onChange={(e) => setForm({ ...form, text: e.target.value })} required />
            </Field>
          ) : (
            <>
              <Field label={isLocal ? "Absolute path" : form.source_type === "github" ? "Repository (owner/name)" : "Project path (group/name)"}>
                <Input value={form.location} onChange={(e) => setForm({ ...form, location: e.target.value })} required
                  placeholder={isLocal ? "D:\\Code\\my-app" : "owner/repo"} />
              </Field>
              {["local_git", "github", "gitlab"].includes(form.source_type) && (
                <Field label="Branch (optional)"><Input value={form.branch} onChange={(e) => setForm({ ...form, branch: e.target.value })} placeholder="main" /></Field>
              )}
              {["github", "gitlab"].includes(form.source_type) && (
                <Field label="Read-only access token (optional)" hint="Needed for private repositories. Stored encrypted.">
                  <Input type="password" value={form.token} onChange={(e) => setForm({ ...form, token: e.target.value })} autoComplete="off" />
                </Field>
              )}
            </>
          )}
          <ErrorBox error={add.error} />
          <Button type="submit" loading={add.isPending}>Add source</Button>
        </form>
      </Dialog>
    </Card>
  );
}

function Knowledge({ appId }: { appId: number }) {
  const { data, refetch } = useApi(["knowledge", appId], `/api/applications/${appId}/knowledge`);
  const [facts, setFacts] = useState<any[] | null>(null);
  const [summary, setSummary] = useState("");
  useEffect(() => {
    if (data?.knowledge) {
      setFacts(data.knowledge.facts.filter((f: any) => f.active !== false));
      setSummary(data.knowledge.summary);
    }
  }, [data]);
  const save = useAction(() => api.patch(`/api/applications/${appId}/knowledge`, { summary, facts }), [["knowledge", appId]], "Knowledge profile saved");
  if (!data) return <Spinner />;
  const k = data.knowledge;
  return (
    <div className="grid gap-6 xl:grid-cols-3">
      <Card className="xl:col-span-2">
        <CardHeader>
          <div>
            <CardTitle>Knowledge profile {k && <span className="text-muted-foreground">v{k.version}</span>}</CardTitle>
            <CardDescription>
              {k ? `Built by ${k.generated_by}; updated ${fmtRelative(k.updated_at)}. Facts are the only claims videos may make.` : "Run an analysis to build the profile."}
            </CardDescription>
          </div>
          {k && <Button size="sm" onClick={() => save.mutate()} loading={save.isPending}>Save edits</Button>}
        </CardHeader>
        {k && facts && (
          <CardContent className="space-y-4">
            <Field label="Summary"><Textarea rows={3} value={summary} onChange={(e) => setSummary(e.target.value)} /></Field>
            {(k.profile?.recently_shipped?.length ?? 0) > 0 && (
              <div><p className="mb-1 text-xs font-medium text-muted-foreground">Recently shipped</p>
                <div className="flex flex-wrap gap-1.5">{k.profile.recently_shipped.map((r: string) => <Badge key={r} tone="primary">{r}</Badge>)}</div></div>
            )}
            <div className="space-y-2">
              <p className="text-xs font-medium text-muted-foreground">Facts ({facts.length})</p>
              {facts.map((f, i) => (
                <div key={f.id ?? `new${i}`} className="flex items-center gap-2">
                  <span className="w-12 shrink-0 font-mono text-xs text-muted-foreground">{f.id ?? "new"}</span>
                  <Input value={f.text} onChange={(e) => setFacts(facts.map((x, j) => (j === i ? { ...x, text: e.target.value } : x)))} />
                  <Select className="w-32" value={f.kind} onChange={(e) => setFacts(facts.map((x, j) => (j === i ? { ...x, kind: e.target.value } : x)))}>
                    {["feature", "benefit", "integration", "audience", "usp", "limitation"].map((x) => <option key={x}>{x}</option>)}
                  </Select>
                  <Select className="w-28" value={f.status} onChange={(e) => setFacts(facts.map((x, j) => (j === i ? { ...x, status: e.target.value } : x)))}>
                    {["released", "beta", "planned"].map((x) => <option key={x}>{x}</option>)}
                  </Select>
                  <span className="w-10 text-center text-xs text-muted-foreground" title="times featured">{f.times_featured ?? 0}×</span>
                  <Button size="icon" variant="ghost" onClick={() => setFacts(facts.filter((_, j) => j !== i))}><Trash2 /></Button>
                </div>
              ))}
              <Button size="sm" variant="outline" onClick={() => setFacts([...facts, { text: "", kind: "feature", status: "released" }])}><Plus /> Add fact</Button>
            </div>
          </CardContent>
        )}
      </Card>
      <Card>
        <CardHeader>
          <div><CardTitle>Detected changes</CardTitle><CardDescription>Classified locally; only significant ones reach Claude.</CardDescription></div>
          <Button size="icon" variant="ghost" onClick={() => refetch()}><RefreshCw /></Button>
        </CardHeader>
        <CardContent className="max-h-[560px] space-y-2 overflow-y-auto scrollbar-thin">
          {data.changes.length === 0 && <p className="text-sm text-muted-foreground">No changes recorded yet.</p>}
          {data.changes.map((c: any) => (
            <div key={c.id} className="rounded-lg border p-2.5">
              <div className="flex items-center justify-between gap-2">
                <Badge tone={c.category === "feature" ? "success" : c.category === "release" ? "primary" : "default"}>{c.category}</Badge>
                <span className="text-[11px] text-muted-foreground">sig {c.significance.toFixed(2)}{c.initial ? " · history" : ""}</span>
              </div>
              <p className="mt-1.5 text-sm">{c.title}</p>
              <p className="mt-1 text-[11px] text-muted-foreground">{fmtDate(c.detected_at)} · {c.processed ? "processed" : "pending"}{c.promoted ? " · promoted" : ""}</p>
            </div>
          ))}
        </CardContent>
      </Card>
    </div>
  );
}

function Assets({ appId }: { appId: number }) {
  const { data } = useApi<any[]>(["assets", appId], `/api/applications/${appId}/assets`);
  const [kind, setKind] = useState("screenshot");
  const [license, setLicense] = useState("");
  const [drag, setDrag] = useState(false);
  const [busy, setBusy] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const { toast } = useToast();
  const qc = useQueryClient();
  const del = useAction((id: number) => api.del(`/api/assets/${id}`), [["assets", appId]], "Asset removed");
  const patch = useAction(({ id, ...v }: any) => api.patch(`/api/assets/${id}`, v), [["assets", appId]]);
  const upload = async (files: FileList | null) => {
    if (!files?.length) return;
    setBusy(true);
    try {
      for (const f of Array.from(files)) {
        const fd = new FormData();
        fd.append("file", f);
        fd.append("kind", kind);
        fd.append("license_note", license);
        await api.upload(`/api/applications/${appId}/assets`, fd);
      }
      toast(`${files.length} file(s) uploaded`, "success");
    } catch (e: any) {
      toast(e.message, "error");
    } finally {
      setBusy(false);
      qc.invalidateQueries({ queryKey: ["assets", appId] });
      if (input.current) input.current.value = "";
    }
  };
  return (
    <div className="space-y-6">
      <Card className="p-5">
        <div className="grid gap-4 sm:grid-cols-3">
          <Field label="Asset type">
            <Select value={kind} onChange={(e) => setKind(e.target.value)}>
              <option value="screenshot">Product screenshot</option><option value="logo">Logo</option>
              <option value="demo_video">Demo video clip</option><option value="music">Background music (licensed)</option>
              <option value="intro">Intro clip</option><option value="outro">Outro clip</option>
            </Select>
          </Field>
          {kind === "music" && <Field label="License note (required)" className="sm:col-span-2"><Input value={license} onChange={(e) => setLicense(e.target.value)} placeholder="e.g. Pixabay Content License, track URL" /></Field>}
        </div>
        <div
          onDragOver={(e) => { e.preventDefault(); setDrag(true); }}
          onDragLeave={() => setDrag(false)}
          onDrop={(e) => { e.preventDefault(); setDrag(false); upload(e.dataTransfer.files); }}
          onClick={() => input.current?.click()}
          className={`mt-4 flex cursor-pointer flex-col items-center justify-center rounded-xl border-2 border-dashed p-8 text-center transition-colors ${drag ? "border-primary bg-primary/5" : "hover:bg-muted/50"}`}
        >
          {busy ? <Spinner /> : <Upload className="size-6 text-muted-foreground" />}
          <p className="mt-2 text-sm font-medium">Drop files here or click to upload</p>
          <p className="text-xs text-muted-foreground">PNG, JPG, WebP, MP4, MOV, WebM, MP3, WAV · up to 300 MB</p>
          <input ref={input} type="file" multiple hidden onChange={(e) => upload(e.target.files)} />
        </div>
      </Card>
      <AssetGrid data={data} onDelete={(id) => confirm("Delete asset?") && del.mutate(id)} onDescribe={(id, description) => patch.mutate({ id, description })} />
    </div>
  );
}

function AssetGrid({ data, onDelete, onDescribe }: { data?: any[]; onDelete: (id: number) => void; onDescribe: (id: number, d: string) => void }) {
  if (!data) return <Spinner />;
  if (!data.length) return <Empty title="No assets">Screenshots make the most convincing scenes; the AI references them by description.</Empty>;
  return (
    <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
      {data.map((a) => (
        <Card key={a.id} className="overflow-hidden">
          <div className="flex aspect-video items-center justify-center bg-muted">
            {a.mime.startsWith("image/") ? <img src={a.url} className="h-full w-full object-contain" alt={a.original_name} />
              : a.mime.startsWith("video/") ? <video src={a.url} className="h-full w-full object-contain" muted controls preload="metadata" />
              : <audio src={a.url} controls className="w-11/12" />}
          </div>
          <div className="space-y-2 p-3">
            <div className="flex items-center justify-between gap-2">
              <Badge tone="primary">{a.kind}</Badge>
              <Button size="icon" variant="ghost" onClick={() => onDelete(a.id)}><Trash2 /></Button>
            </div>
            <p className="truncate text-xs text-muted-foreground" title={a.original_name}>{a.original_name}</p>
            {a.license_note && <p className="text-[11px] text-muted-foreground">License: {a.license_note}</p>}
            <Input defaultValue={a.description} placeholder="Describe what it shows" className="h-8 text-xs" onBlur={(e) => e.target.value !== a.description && onDescribe(a.id, e.target.value)} />
          </div>
        </Card>
      ))}
    </div>
  );
}

function Strategy({ app }: { app: any }) {
  const [v, setV] = useState({
    publishing_mode: app.publishing_mode, active: app.active, paused: app.paused,
    content_types: app.content_strategy?.content_types ?? [], goals: app.content_strategy?.goals ?? "",
    video: { target_duration: "", scenes: "", ...app.video_settings },
    budget: { daily_calls: "", monthly_tokens: "", monthly_cost_usd: "", ...app.ai_budget },
  });
  const save = useAction(() => {
    const clean = (o: any) => Object.fromEntries(Object.entries(o).filter(([, x]) => x !== "" && x !== null).map(([k, x]) => [k, isNaN(Number(x)) ? x : Number(x)]));
    return api.patch(`/api/applications/${app.id}`, {
      publishing_mode: v.publishing_mode, active: v.active, paused: v.paused,
      content_strategy: { ...app.content_strategy, content_types: v.content_types, goals: v.goals },
      video_settings: clean(v.video), ai_budget: clean(v.budget),
    });
  }, [["application", app.id]], "Strategy saved");
  return (
    <div className="grid gap-6 lg:grid-cols-2">
      <Card>
        <CardHeader><div><CardTitle>Publishing & automation</CardTitle><CardDescription>Account-level modes override this.</CardDescription></div></CardHeader>
        <CardContent className="space-y-4">
          <Field label="Publishing mode" hint="Manual: approve every video and post. Semi: videos auto-approved after QC, posts need approval. Automatic: publish after QC for authorized accounts.">
            <Select value={v.publishing_mode} onChange={(e) => setV({ ...v, publishing_mode: e.target.value })}>
              <option value="manual">Manual</option><option value="semi">Semi-automatic</option><option value="auto">Automatic</option>
            </Select>
          </Field>
          <div className="flex gap-6"><Switch checked={v.active} onChange={(x) => setV({ ...v, active: x })} label="Active" />
            <Switch checked={v.paused} onChange={(x) => setV({ ...v, paused: x })} label="Pause all schedules" /></div>
          <Field label="Campaign goals (used for topic selection context)"><Textarea rows={3} value={v.goals} onChange={(e) => setV({ ...v, goals: e.target.value })} /></Field>
          <Field label="Allowed content types" hint="None selected = all types rotate.">
            <div className="flex flex-wrap gap-1.5">
              {CONTENT_TYPES.map((t) => {
                const on = v.content_types.includes(t);
                return <button key={t} type="button" onClick={() => setV({ ...v, content_types: on ? v.content_types.filter((x: string) => x !== t) : [...v.content_types, t] })}
                  className={`rounded-md border px-2 py-1 text-xs ${on ? "border-primary bg-primary/10 text-primary" : "text-muted-foreground"}`}>{label(t)}</button>;
              })}
            </div>
          </Field>
        </CardContent>
      </Card>
      <Card>
        <CardHeader><div><CardTitle>Overrides</CardTitle><CardDescription>Leave blank to use global settings.</CardDescription></div></CardHeader>
        <CardContent className="space-y-4">
          <div className="grid grid-cols-2 gap-4">
            <Field label="Target duration (s)"><Input type="number" value={v.video.target_duration} onChange={(e) => setV({ ...v, video: { ...v.video, target_duration: e.target.value } })} /></Field>
            <Field label="Scenes per video"><Input type="number" value={v.video.scenes} onChange={(e) => setV({ ...v, video: { ...v.video, scenes: e.target.value } })} /></Field>
          </div>
          <p className="text-xs font-medium text-muted-foreground">Claude budget for this application</p>
          <div className="grid grid-cols-3 gap-3">
            <Field label="Daily calls"><Input type="number" value={v.budget.daily_calls} onChange={(e) => setV({ ...v, budget: { ...v.budget, daily_calls: e.target.value } })} /></Field>
            <Field label="Monthly tokens"><Input type="number" value={v.budget.monthly_tokens} onChange={(e) => setV({ ...v, budget: { ...v.budget, monthly_tokens: e.target.value } })} /></Field>
            <Field label="Monthly USD"><Input type="number" step="0.01" value={v.budget.monthly_cost_usd} onChange={(e) => setV({ ...v, budget: { ...v.budget, monthly_cost_usd: e.target.value } })} /></Field>
          </div>
          <ErrorBox error={save.error} />
          <Button onClick={() => save.mutate()} loading={save.isPending}>Save strategy</Button>
        </CardContent>
      </Card>
    </div>
  );
}

export function ApplicationDetail() {
  const { id } = useParams();
  const appId = Number(id);
  const navigate = useNavigate();
  const [tab, setTab] = useState("knowledge");
  const [editOpen, setEditOpen] = useState(false);
  const { data: app } = useApi(["application", appId], `/api/applications/${appId}`);
  const { data: jobs } = useApi<any[]>(["jobs", "app", appId], `/api/jobs?application_id=${appId}&limit=5`, { refetchInterval: 3000 });
  const analyze = useAction((full: boolean) => api.post(`/api/applications/${appId}/analyze${full ? "?full=true" : ""}`), [["jobs", "app", appId]], "Analysis queued");
  const generate = useAction(() => api.post("/api/studio/generate", { application_id: appId }), [["jobs", "app", appId]], "Video generation queued");
  const update = useAction((v: any) => api.patch(`/api/applications/${appId}`, v), [["application", appId], ["applications"]], "Saved");
  const remove = useAction(() => api.del(`/api/applications/${appId}`), [["applications"]], "Application deleted");
  if (!app) return <Spinner />;
  const active = jobs?.find((j) => ["running", "queued"].includes(j.status));
  return (
    <div>
      <PageHeader title={app.name} description={app.description || app.category}
        actions={<>
          <Button variant="outline" onClick={() => setEditOpen(true)}>Edit details</Button>
          <Button variant="outline" onClick={() => analyze.mutate(false)} loading={analyze.isPending}><RefreshCw /> Analyze</Button>
          <Button variant="outline" onClick={() => confirm("Rebuild the profile from scratch? (one larger Claude call)") && analyze.mutate(true)}>Full re-analysis</Button>
          <Button onClick={() => generate.mutate()} loading={generate.isPending}><Sparkles /> Generate video</Button>
        </>} />
      {active && (
        <div className="mb-4 flex items-center gap-3 rounded-lg border bg-primary/5 px-4 py-2.5 text-sm">
          <Play className="size-4 text-primary" /> {label(active.job_type)} #{active.id}: <StatusBadge status={active.status} />
          <span className="truncate text-muted-foreground">{active.progress_message}</span>
        </div>
      )}
      <Tabs value={tab} onChange={setTab} tabs={[{ id: "knowledge", label: "Knowledge" }, { id: "sources", label: "Sources", count: app.sources },
        { id: "assets", label: "Assets" }, { id: "strategy", label: "Strategy & budget" }]} />
      <div className="mt-6">
        {tab === "knowledge" && <Knowledge appId={appId} />}
        {tab === "sources" && <Sources appId={appId} />}
        {tab === "assets" && <Assets appId={appId} />}
        {tab === "strategy" && <Strategy app={app} />}
      </div>
      <Dialog open={editOpen} onClose={() => setEditOpen(false)} title="Edit application" wide>
        <AppForm initial={app} submitLabel="Save" busy={update.isPending} error={update.error}
          onSubmit={async (v) => { await update.mutateAsync(v); setEditOpen(false); }} />
        <div className="mt-6 border-t pt-4">
          <Button variant="destructive" onClick={async () => {
            if (confirm(`Delete ${app.name} and all its content, schedules and account connections?`)) {
              await remove.mutateAsync();
              navigate("/applications");
            }
          }}><Trash2 /> Delete application</Button>
        </div>
      </Dialog>
    </div>
  );
}

export { AssetGrid };
