import { useEffect, useState } from "react";
import { AlertTriangle, CheckCircle2, Clapperboard, RefreshCw, Sparkles, Wand2 } from "lucide-react";
import { api } from "@/lib/api";
import { useAction, useApi } from "@/lib/hooks";
import { CONTENT_TYPES, fmtRelative, label } from "@/lib/utils";
import {
  Badge, Button, Card, CardContent, CardDescription, CardHeader, CardTitle, Empty, Field, Input, PageHeader, Progress,
  Select, Spinner, StatusBadge, Switch, Textarea,
} from "@/components/ui";

export function StoryboardEditor({ script, onSaved }: { script: any; onSaved?: () => void }) {
  const [scenes, setScenes] = useState<any[]>(script.storyboard?.scenes ?? []);
  const [social, setSocial] = useState<any>(script.social_content ?? {});
  const [regen, setRegen] = useState<number[]>([]);
  useEffect(() => { setScenes(script.storyboard?.scenes ?? []); setSocial(script.social_content ?? {}); }, [script.id, script.storyboard?.id]);
  const save = useAction(() => api.put(`/api/scripts/${script.id}`, { scenes, social_content: social }), [["scripts"], ["videos"]], "Script saved (new storyboard version)");
  const render = useAction((all: boolean) => api.post(`/api/storyboards/${script.storyboard.id}/render`, { scenes: regen, all }), [["jobs"], ["scripts"]], "Render queued");
  const newVersion = useAction(() => api.post(`/api/scripts/${script.id}/new-version`), [["jobs"]], "New version queued");
  const set = (i: number, k: string, v: any) => setScenes(scenes.map((s, j) => (j === i ? { ...s, [k]: v } : s)));
  const cc = script.claim_check ?? {};
  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone="primary">{label(script.idea?.content_type ?? "")}</Badge>
        <Badge>{script.idea?.source === "claude" ? "Claude plan" : "Local template plan"}</Badge>
        {cc.ok ? <Badge tone="success"><CheckCircle2 className="size-3" /> claims verified</Badge> : <Badge tone="danger"><AlertTriangle className="size-3" /> {cc.errors} claim errors</Badge>}
        {cc.warnings > 0 && <Badge tone="warning">{cc.warnings} warnings</Badge>}
        {typeof cc.similarity === "number" && <Badge>similarity {Math.round(cc.similarity * 100)}%</Badge>}
      </div>
      {(cc.issues ?? []).length > 0 && (
        <ul className="space-y-1 rounded-lg border bg-muted/40 p-3 text-xs">
          {cc.issues.map((i: any, k: number) => <li key={k} className={i.severity === "error" ? "text-danger" : "text-warning"}>• {i.text}</li>)}
        </ul>
      )}
      <div className="space-y-3">
        {scenes.map((s, i) => (
          <Card key={i} className="p-4">
            <div className="mb-3 flex items-center justify-between">
              <span className="text-sm font-semibold">Scene {i + 1}</span>
              <div className="flex items-center gap-3">
                <Select className="h-8 w-48 text-xs" value={s.visual_source} onChange={(e) => set(i, "visual_source", e.target.value)}>
                  <option value="comfyui">ComfyUI generated</option><option value="application_screenshot">Product screenshot</option><option value="title_card">Title card</option>
                </Select>
                <Input className="h-8 w-20 text-xs" type="number" step="0.5" min={1.5} max={15} value={s.duration_seconds} onChange={(e) => set(i, "duration_seconds", Number(e.target.value))} />
                <Switch checked={regen.includes(s.scene_number)} onChange={(v) => setRegen(v ? [...regen, s.scene_number] : regen.filter((x) => x !== s.scene_number))} label="Regenerate" />
              </div>
            </div>
            <div className="grid gap-3 md:grid-cols-2">
              <Field label="Narration"><Textarea rows={2} value={s.narration} onChange={(e) => set(i, "narration", e.target.value)} /></Field>
              <div className="space-y-3">
                <Field label="Overlay text"><Input value={s.overlay_text} onChange={(e) => set(i, "overlay_text", e.target.value)} /></Field>
                {s.visual_source === "comfyui" && <Field label="Visual prompt"><Input value={s.visual_prompt} onChange={(e) => set(i, "visual_prompt", e.target.value)} /></Field>}
                {s.visual_source === "application_screenshot" && <Field label="Screenshot asset id"><Input type="number" value={s.screenshot_asset_id} onChange={(e) => set(i, "screenshot_asset_id", Number(e.target.value))} /></Field>}
              </div>
            </div>
          </Card>
        ))}
      </div>
      <div className="grid gap-4 md:grid-cols-3">
        {["instagram", "tiktok"].map((p) => (
          <Field key={p} label={`${label(p)} caption`}>
            <Textarea rows={4} value={social[p]?.caption ?? ""} onChange={(e) => setSocial({ ...social, [p]: { ...social[p], caption: e.target.value } })} />
            <Input className="mt-2" value={(social[p]?.hashtags ?? []).join(" ")} placeholder="#hashtags"
              onChange={(e) => setSocial({ ...social, [p]: { ...social[p], hashtags: e.target.value.split(/\s+/).filter(Boolean) } })} />
          </Field>
        ))}
        <Field label="YouTube title & description">
          <Input value={social.youtube?.title ?? ""} maxLength={100} onChange={(e) => setSocial({ ...social, youtube: { ...social.youtube, title: e.target.value } })} />
          <Textarea className="mt-2" rows={3} value={social.youtube?.description ?? ""} onChange={(e) => setSocial({ ...social, youtube: { ...social.youtube, description: e.target.value } })} />
        </Field>
      </div>
      <div className="flex flex-wrap gap-2">
        <Button onClick={async () => { await save.mutateAsync(); onSaved?.(); }} loading={save.isPending}>Save edits</Button>
        <Button variant="outline" onClick={() => render.mutate(false)} loading={render.isPending}><Clapperboard /> Render{regen.length ? ` (regenerate scenes ${regen.join(", ")})` : ""}</Button>
        <Button variant="outline" onClick={() => render.mutate(true)}><RefreshCw /> Re-render all visuals</Button>
        <Button variant="ghost" onClick={() => confirm("Create a completely new script for this topic?") && newVersion.mutate()}><Wand2 /> New script version</Button>
      </div>
      <p className="text-xs text-muted-foreground">Save edits first; rendering always uses the latest saved storyboard.</p>
    </div>
  );
}

export function Studio() {
  const { data: apps } = useApi<any[]>(["applications"], "/api/applications");
  const [form, setForm] = useState({ application_id: "", content_type: "", topic: "", force_local: false });
  const [selected, setSelected] = useState<number | null>(null);
  const { data: scripts, refetch } = useApi<any[]>(["scripts"], `/api/scripts?limit=30`, { refetchInterval: 8000 });
  const { data: jobs } = useApi<any[]>(["jobs", "studio"], "/api/jobs?job_type=plan_content&limit=5", { refetchInterval: 2500 });
  const { data: renders } = useApi<any[]>(["jobs", "renders"], "/api/jobs?job_type=render_video&limit=5", { refetchInterval: 2500 });
  const gen = useAction(() => api.post("/api/studio/generate", { ...form, application_id: Number(form.application_id), content_type: form.content_type || null, topic: form.topic || null }),
    [["jobs"]], "Generation queued: planning, then rendering");
  const current = scripts?.find((s) => s.id === selected) ?? scripts?.[0];
  const active = [...(jobs ?? []), ...(renders ?? [])].filter((j) => ["queued", "running", "retry_wait", "blocked"].includes(j.status));
  return (
    <div>
      <PageHeader title="AI Content Studio" description="Generate a video on demand, then refine its script, storyboard and captions." />
      <div className="grid gap-6 xl:grid-cols-3">
        <div className="space-y-6">
          <Card>
            <CardHeader><div><CardTitle>Generate video</CardTitle><CardDescription>One structured Claude call (or the free local planner), then local rendering.</CardDescription></div></CardHeader>
            <CardContent className="space-y-4">
              <Field label="Application">
                <Select value={form.application_id} onChange={(e) => setForm({ ...form, application_id: e.target.value })}>
                  <option value="">Select…</option>{apps?.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
                </Select>
              </Field>
              <Field label="Content type"><Select value={form.content_type} onChange={(e) => setForm({ ...form, content_type: e.target.value })}>
                <option value="">Automatic (rotates by strategy)</option>{CONTENT_TYPES.map((t) => <option key={t} value={t}>{label(t)}</option>)}
              </Select></Field>
              <Field label="Topic (optional)" hint="Leave empty to let the system pick fresh changes or least-featured facts.">
                <Input value={form.topic} onChange={(e) => setForm({ ...form, topic: e.target.value })} />
              </Field>
              <Switch checked={form.force_local} onChange={(v) => setForm({ ...form, force_local: v })} label="Use local planner (no Claude tokens)" />
              <Button className="w-full" disabled={!form.application_id} loading={gen.isPending} onClick={() => gen.mutate()}><Sparkles /> Generate video</Button>
            </CardContent>
          </Card>
          {active.length > 0 && (
            <Card className="p-4 space-y-3">
              {active.map((j) => (
                <div key={j.id}>
                  <div className="flex items-center justify-between text-sm"><span>{label(j.job_type)} #{j.id}</span><StatusBadge status={j.status} /></div>
                  <p className="truncate text-xs text-muted-foreground">{j.blocked_reason || j.progress_message}</p>
                  {j.status === "running" && <Progress value={j.progress} className="mt-1.5" />}
                </div>
              ))}
            </Card>
          )}
          <Card>
            <CardHeader><CardTitle>Recent scripts</CardTitle><Button size="icon" variant="ghost" onClick={() => refetch()}><RefreshCw /></Button></CardHeader>
            <CardContent className="max-h-[520px] space-y-1.5 overflow-y-auto scrollbar-thin">
              {!scripts ? <Spinner /> : scripts.length === 0 ? <p className="text-sm text-muted-foreground">No scripts yet.</p> :
                scripts.map((s) => (
                  <button key={s.id} onClick={() => setSelected(s.id)} className={`block w-full rounded-lg border p-2.5 text-left hover:bg-muted/50 ${current?.id === s.id ? "border-primary bg-primary/5" : ""}`}>
                    <p className="truncate text-sm font-medium">{s.idea?.topic}</p>
                    <p className="text-xs text-muted-foreground">{apps?.find((a) => a.id === s.application_id)?.name} · {fmtRelative(s.created_at)} · {s.videos.length} render(s)</p>
                  </button>
                ))}
            </CardContent>
          </Card>
        </div>
        <div className="xl:col-span-2">
          {!current ? <Empty icon={Sparkles} title="Nothing generated yet">Pick an application and generate your first video.</Empty> : (
            <Card>
              <CardHeader>
                <div><CardTitle>{current.idea?.topic}</CardTitle><CardDescription>Hook: “{current.hook}” · {Math.round(current.duration_seconds)}s · {current.storyboard?.scenes.length} scenes</CardDescription></div>
              </CardHeader>
              <CardContent className="space-y-6">
                {current.videos.length > 0 && (
                  <div className="flex gap-4 overflow-x-auto pb-2 scrollbar-thin">
                    {current.videos.map((v: any) => (
                      <div key={v.id} className="w-44 shrink-0">
                        {v.url ? <video src={v.url} controls preload="metadata" poster={v.thumbnail_url ?? undefined} className="aspect-[9/16] w-full rounded-lg bg-black object-cover" />
                          : <div className="flex aspect-[9/16] items-center justify-center rounded-lg bg-muted text-xs text-muted-foreground">{v.status}</div>}
                        <div className="mt-1.5 flex items-center justify-between text-xs"><span>#{v.id}</span><StatusBadge status={v.status} /></div>
                      </div>
                    ))}
                  </div>
                )}
                <StoryboardEditor script={current} onSaved={() => refetch()} />
              </CardContent>
            </Card>
          )}
        </div>
      </div>
    </div>
  );
}
