import { useEffect, useState } from "react";
import { KeyRound, Plus, Trash2 } from "lucide-react";
import { api } from "@/lib/api";
import { useAction, useApi, useToast } from "@/lib/hooks";
import { Badge, Button, Card, CardContent, CardDescription, CardHeader, CardTitle, Field, Input, PageHeader, Select, Spinner, Switch, Tabs } from "@/components/ui";
import { StatusChecks } from "./SetupWizard";

function SecretField({ name, status, label, placeholder }: { name: string; status: any; label: string; placeholder?: string }) {
  const [v, setV] = useState("");
  const save = useAction(() => api.put("/api/secrets", { name, value: v }), [["settings"]], "Secret saved");
  const del = useAction(() => api.del(`/api/secrets/${name}`), [["settings"]], "Secret removed");
  return (
    <Field label={label} hint={status?.configured ? `Configured (${status.hint}) · stored encrypted, never shown again` : "Not configured"}>
      <div className="flex gap-2">
        <Input type="password" value={v} onChange={(e) => setV(e.target.value)} placeholder={status?.configured ? "Enter a new value to rotate" : placeholder} autoComplete="off" />
        <Button disabled={!v} loading={save.isPending} onClick={async () => { await save.mutateAsync(); setV(""); }}><KeyRound /> Save</Button>
        {status?.configured && <Button variant="ghost" size="icon" onClick={() => confirm("Remove this secret?") && del.mutate()}><Trash2 /></Button>}
      </div>
    </Field>
  );
}

function useSection(data: any, name: string) {
  const [v, setV] = useState<any>(null);
  useEffect(() => { if (data) setV(structuredClone(data.settings[name])); }, [data, name]);
  const save = useAction((patch?: any) => api.patch(`/api/settings/${name}`, patch ?? v), [["settings"], ["system-status"]], "Settings saved");
  return { v, setV, save };
}

function Num({ label, value, onChange, step, hint }: any) {
  return <Field label={label} hint={hint}><Input type="number" step={step ?? 1} value={value ?? ""} onChange={(e) => onChange(e.target.value === "" ? null : Number(e.target.value))} /></Field>;
}

export function SettingsPage() {
  const { data } = useApi(["settings"], "/api/settings");
  const { data: voices } = useApi<any[]>(["voices"], "/api/settings/voices");
  const [tab, setTab] = useState("ai");
  const { toast } = useToast();
  const ai = useSection(data, "ai");
  const comfy = useSection(data, "comfyui");
  const video = useSection(data, "video");
  const gen = useSection(data, "general");
  const sec = useSection(data, "security");
  const sch = useSection(data, "scheduler");
  const dd = useSection(data, "dedup");
  const oauth = useSection(data, "oauth");
  const notif = useSection(data, "notifications");
  const [newRoot, setNewRoot] = useState("");
  const [pw, setPw] = useState({ current_password: "", new_password: "" });
  const testClaude = useAction(() => api.post("/api/settings/test/claude"), []);
  const changePw = useAction(() => api.post("/api/auth/change-password", pw), [], "Password changed; other sessions signed out");
  const backup = useAction(() => api.post("/api/system/backup"), [["backups"]], "Backup created");
  const { data: backups } = useApi<any[]>(["backups"], "/api/system/backups", { enabled: tab === "system" });
  if (!data || !ai.v) return <Spinner />;
  const s = data.secrets;
  const origin = window.location.origin;
  return (
    <div>
      <PageHeader title="Settings" description={`Secrets are encrypted with a master key held in: ${data.key_storage}.`} />
      <Tabs value={tab} onChange={setTab} tabs={[{ id: "ai", label: "AI" }, { id: "comfyui", label: "ComfyUI" }, { id: "video", label: "Video" },
        { id: "social", label: "Social platforms" }, { id: "automation", label: "Automation" }, { id: "security", label: "Security" }, { id: "system", label: "System" }]} />
      <div className="mt-6 grid gap-6 lg:grid-cols-2">
        {tab === "ai" && <>
          <Card>
            <CardHeader><div><CardTitle>Claude</CardTitle><CardDescription>Used only for profile building, change summaries and one structured plan per video.</CardDescription></div></CardHeader>
            <CardContent className="space-y-4">
              <SecretField name="anthropic.api_key" status={s["anthropic.api_key"]} label="Anthropic API key" placeholder="sk-ant-…" />
              <Button size="sm" variant="outline" loading={testClaude.isPending} onClick={async () => { const r: any = await testClaude.mutateAsync(); toast(r.ok ? `Connected: ${r.display_name}` : r.error, r.ok ? "success" : "error"); }}>Test connection</Button>
              <div className="grid grid-cols-2 gap-4">
                <Field label="Model"><Select value={ai.v.model} onChange={(e) => ai.setV({ ...ai.v, model: e.target.value })}>
                  {Object.keys(ai.v.pricing).map((m) => <option key={m}>{m}</option>)}</Select></Field>
                <Field label="Effort"><Select value={ai.v.effort} onChange={(e) => ai.setV({ ...ai.v, effort: e.target.value })}>
                  <option>low</option><option>medium</option><option>high</option></Select></Field>
                <Num label="Max output tokens" value={ai.v.max_output_tokens} onChange={(x: any) => ai.setV({ ...ai.v, max_output_tokens: x })} />
                <Num label="Change significance threshold" step={0.05} value={ai.v.significance_threshold} onChange={(x: any) => ai.setV({ ...ai.v, significance_threshold: x })} hint="0-1; below this, changes never reach Claude" />
                <Num label="Initial profile context (chars)" value={ai.v.profile_char_budget} onChange={(x: any) => ai.setV({ ...ai.v, profile_char_budget: x })} />
                <Num label="Change digest context (chars)" value={ai.v.digest_char_budget} onChange={(x: any) => ai.setV({ ...ai.v, digest_char_budget: x })} />
              </div>
              <Switch checked={ai.v.enabled} onChange={(x) => ai.setV({ ...ai.v, enabled: x })} label="AI enabled" />
              <Switch checked={ai.v.cost_saving_mode} onChange={(x) => ai.setV({ ...ai.v, cost_saving_mode: x })} label="Cost-saving mode (local planner for videos; Claude only for significant profile updates)" />
              <Switch checked={ai.v.use_refusal_fallbacks} onChange={(x) => ai.setV({ ...ai.v, use_refusal_fallbacks: x })} label="Server-side refusal fallbacks" />
              <Button onClick={() => ai.save.mutate(undefined)} loading={ai.save.isPending}>Save AI settings</Button>
            </CardContent>
          </Card>
          <Card>
            <CardHeader><div><CardTitle>Spending limits</CardTitle><CardDescription>Checked before every request. Per-application limits live on each application.</CardDescription></div></CardHeader>
            <CardContent className="space-y-4">
              <div className="grid grid-cols-2 gap-4">
                {[["daily_calls", "Daily Claude calls"], ["monthly_tokens", "Monthly tokens"], ["monthly_cost_usd", "Monthly spend (USD)"], ["max_tokens_per_job", "Max tokens per job"], ["max_retries", "Max corrective retries per plan"]].map(([k, l]) => (
                  <Num key={k} label={l} step={k === "monthly_cost_usd" ? 0.5 : 1} value={ai.v.budgets[k]} onChange={(x: any) => ai.setV({ ...ai.v, budgets: { ...ai.v.budgets, [k]: x } })} />
                ))}
                <Field label="When a limit is reached"><Select value={ai.v.on_budget_exceeded} onChange={(e) => ai.setV({ ...ai.v, on_budget_exceeded: e.target.value })}>
                  <option value="pause">Pause AI jobs until the window resets</option><option value="local_fallback">Continue with the local planner (no spend)</option></Select></Field>
              </div>
              <p className="text-xs font-medium text-muted-foreground">Pricing used for estimates (USD per 1M tokens: input / output / cache read)</p>
              {Object.entries(ai.v.pricing).map(([m, p]: any) => (
                <div key={m} className="grid grid-cols-[1fr_80px_80px_80px] items-center gap-2 text-xs">
                  <span className="font-mono">{m}</span>
                  {[0, 1, 2].map((i) => <Input key={i} className="h-8" type="number" step="0.01" value={p[i]} onChange={(e) => { const np = [...p]; np[i] = Number(e.target.value); ai.setV({ ...ai.v, pricing: { ...ai.v.pricing, [m]: np } }); }} />)}
                </div>
              ))}
              <Button onClick={() => ai.save.mutate(undefined)} loading={ai.save.isPending}>Save limits</Button>
            </CardContent>
          </Card>
        </>}

        {tab === "comfyui" && comfy.v && (
          <Card>
            <CardHeader><div><CardTitle>ComfyUI connection</CardTitle><CardDescription>Keep ComfyUI bound to localhost; it is never exposed by this app.</CardDescription></div></CardHeader>
            <CardContent className="space-y-4">
              <Field label="Server URL"><Input value={comfy.v.url} onChange={(e) => comfy.setV({ ...comfy.v, url: e.target.value })} /></Field>
              <div className="grid grid-cols-2 gap-4">
                <Num label="Default timeout (s)" value={comfy.v.timeout_seconds} onChange={(x: any) => comfy.setV({ ...comfy.v, timeout_seconds: x })} />
                <Num label="Max ComfyUI scenes per video" value={comfy.v.max_comfy_scenes_per_video} onChange={(x: any) => comfy.setV({ ...comfy.v, max_comfy_scenes_per_video: x })} hint="Limits GPU time per video" />
              </div>
              <Switch checked={comfy.v.enabled} onChange={(x) => comfy.setV({ ...comfy.v, enabled: x })} label="Use ComfyUI" />
              <Switch checked={comfy.v.use_for_scenes} onChange={(x) => comfy.setV({ ...comfy.v, use_for_scenes: x })} label="Generate scene images" />
              <Switch checked={comfy.v.use_img2vid} onChange={(x) => comfy.setV({ ...comfy.v, use_img2vid: x })} label="Animate stills with an img2vid workflow (slow)" />
              <Button onClick={() => comfy.save.mutate(undefined)} loading={comfy.save.isPending}>Save</Button>
            </CardContent>
          </Card>
        )}

        {tab === "video" && video.v && <>
          <Card>
            <CardHeader><CardTitle>Output</CardTitle></CardHeader>
            <CardContent className="space-y-4">
              <div className="grid grid-cols-3 gap-4">
                <Num label="Width" value={video.v.width} onChange={(x: any) => video.setV({ ...video.v, width: x })} />
                <Num label="Height" value={video.v.height} onChange={(x: any) => video.setV({ ...video.v, height: x })} />
                <Num label="FPS" value={video.v.fps} onChange={(x: any) => video.setV({ ...video.v, fps: x })} />
                <Num label="Target duration (s)" value={video.v.target_duration} onChange={(x: any) => video.setV({ ...video.v, target_duration: x })} />
                <Num label="Min duration" value={video.v.min_duration} onChange={(x: any) => video.setV({ ...video.v, min_duration: x })} />
                <Num label="Max duration" value={video.v.max_duration} onChange={(x: any) => video.setV({ ...video.v, max_duration: x })} />
                <Num label="Scenes" value={video.v.scenes} onChange={(x: any) => video.setV({ ...video.v, scenes: x })} />
                <Num label="Quality (CRF)" value={video.v.crf} onChange={(x: any) => video.setV({ ...video.v, crf: x })} hint="lower = better" />
                <Num label="Music volume" step={0.01} value={video.v.music_volume} onChange={(x: any) => video.setV({ ...video.v, music_volume: x })} />
              </div>
              <div className="grid grid-cols-2 gap-4">
                <Field label="Logo position"><Select value={video.v.logo_position} onChange={(e) => video.setV({ ...video.v, logo_position: e.target.value })}>
                  {["top-right", "top-left", "bottom-right", "bottom-left", "none"].map((x) => <option key={x}>{x}</option>)}</Select></Field>
                <Field label="Transitions"><Select value={video.v.transition} onChange={(e) => video.setV({ ...video.v, transition: e.target.value })}><option value="fade">Fade</option><option value="none">Cut</option></Select></Field>
              </div>
              <Switch checked={video.v.ken_burns} onChange={(x) => video.setV({ ...video.v, ken_burns: x })} label="Ken Burns motion on stills" />
              <Button onClick={() => video.save.mutate(undefined)} loading={video.save.isPending}>Save</Button>
            </CardContent>
          </Card>
          <Card>
            <CardHeader><CardTitle>Captions & narration</CardTitle></CardHeader>
            <CardContent className="space-y-4">
              <Switch checked={video.v.caption_style.enabled} onChange={(x) => video.setV({ ...video.v, caption_style: { ...video.v.caption_style, enabled: x } })} label="Burned-in captions" />
              <div className="grid grid-cols-3 gap-4">
                <Num label="Caption size" value={video.v.caption_style.font_size} onChange={(x: any) => video.setV({ ...video.v, caption_style: { ...video.v.caption_style, font_size: x } })} />
                <Field label="Caption color"><input type="color" className="h-9 w-full rounded-lg border" value={video.v.caption_style.color} onChange={(e) => video.setV({ ...video.v, caption_style: { ...video.v.caption_style, color: e.target.value } })} /></Field>
                <Field label="Position"><Select value={video.v.caption_style.position} onChange={(e) => video.setV({ ...video.v, caption_style: { ...video.v.caption_style, position: e.target.value } })}>
                  <option value="lower">Lower third</option><option value="middle">Middle</option><option value="bottom">Bottom</option></Select></Field>
              </div>
              <Switch checked={video.v.voice.enabled} onChange={(x) => video.setV({ ...video.v, voice: { ...video.v.voice, enabled: x } })} label="Local voice narration" />
              <div className="grid grid-cols-2 gap-4">
                <Field label="Engine"><Select value={video.v.voice.engine} onChange={(e) => video.setV({ ...video.v, voice: { ...video.v.voice, engine: e.target.value } })}>
                  <option value="sapi">System voice (Windows SAPI / espeak)</option><option value="piper">Piper</option></Select></Field>
                <Field label="Voice"><Select value={video.v.voice.voice_name} onChange={(e) => video.setV({ ...video.v, voice: { ...video.v.voice, voice_name: e.target.value } })}>
                  <option value="">System default</option>{(voices ?? []).map((x) => <option key={x.id} value={x.name}>{x.name}</option>)}</Select></Field>
                <Num label="Speaking rate (wpm)" value={video.v.voice.rate} onChange={(x: any) => video.setV({ ...video.v, voice: { ...video.v.voice, rate: x } })} />
              </div>
              {video.v.voice.engine === "piper" && <div className="grid grid-cols-2 gap-4">
                <Field label="Piper executable"><Input value={video.v.voice.piper_path} onChange={(e) => video.setV({ ...video.v, voice: { ...video.v.voice, piper_path: e.target.value } })} /></Field>
                <Field label="Piper model (.onnx)"><Input value={video.v.voice.piper_model} onChange={(e) => video.setV({ ...video.v, voice: { ...video.v.voice, piper_model: e.target.value } })} /></Field>
              </div>}
              <Button onClick={() => video.save.mutate(undefined)} loading={video.save.isPending}>Save</Button>
            </CardContent>
          </Card>
        </>}

        {tab === "social" && oauth.v && <>
          <Card className="lg:col-span-2">
            <CardHeader><div><CardTitle>OAuth redirect URIs</CardTitle><CardDescription>Register these in each developer console. See docs/PUBLISHING.md for the full setup.</CardDescription></div></CardHeader>
            <CardContent className="space-y-3">
              <Field label="Redirect base URL" hint={`Leave empty for ${origin.replace("5173", "8000")}. Meta and TikTok require HTTPS: use your tunnel URL here.`}>
                <Input value={oauth.v.redirect_base_url} onChange={(e) => oauth.setV({ ...oauth.v, redirect_base_url: e.target.value })} placeholder="https://autocontent.example.com" />
              </Field>
              {["youtube", "instagram", "tiktok"].map((p) => <p key={p} className="font-mono text-xs">{(oauth.v.redirect_base_url || origin.replace("5173", "8000")).replace(/\/$/, "")}/api/oauth/callback/{p}</p>)}
            </CardContent>
          </Card>
          {[["youtube", "YouTube (Google Cloud OAuth client)", "client_id"], ["instagram", "Instagram (Meta app)", "client_id"], ["tiktok", "TikTok (developer app)", "client_key"]].map(([p, title, idKey]) => (
            <Card key={p}>
              <CardHeader><CardTitle>{title}</CardTitle></CardHeader>
              <CardContent className="space-y-4">
                <Field label={idKey === "client_key" ? "Client key" : p === "instagram" ? "App ID" : "Client ID"}>
                  <Input value={oauth.v[p][idKey] ?? ""} onChange={(e) => oauth.setV({ ...oauth.v, [p]: { ...oauth.v[p], [idKey]: e.target.value } })} />
                </Field>
                {p === "instagram" && <div className="grid grid-cols-2 gap-4">
                  <Field label="Login variant"><Select value={oauth.v.instagram.variant} onChange={(e) => oauth.setV({ ...oauth.v, instagram: { ...oauth.v.instagram, variant: e.target.value } })}>
                    <option value="facebook_login">Facebook Login for Business</option><option value="instagram_login">Instagram Login</option></Select></Field>
                  <Field label="Graph API version"><Input value={oauth.v.instagram.graph_version} onChange={(e) => oauth.setV({ ...oauth.v, instagram: { ...oauth.v.instagram, graph_version: e.target.value } })} /></Field>
                </div>}
                <Button size="sm" variant="outline" onClick={() => oauth.save.mutate(undefined)}>Save {idKey === "client_key" ? "key" : "ID"}</Button>
                <SecretField name={`oauth.${p}.client_secret`} status={s[`oauth.${p}.client_secret`]} label={p === "instagram" ? "App secret" : "Client secret"} />
              </CardContent>
            </Card>
          ))}
          <Card>
            <CardHeader><CardTitle>Repository tokens</CardTitle></CardHeader>
            <CardContent className="space-y-4">
              <SecretField name="github.token" status={s["github.token"]} label="GitHub token (read-only, default for all GitHub sources)" />
              <SecretField name="gitlab.token" status={s["gitlab.token"]} label="GitLab token (read_api)" />
            </CardContent>
          </Card>
        </>}

        {tab === "automation" && sch.v && gen.v && <>
          <Card>
            <CardHeader><CardTitle>Scheduler</CardTitle></CardHeader>
            <CardContent className="space-y-4">
              <div className="grid grid-cols-2 gap-4">
                <Field label="System timezone"><Input value={gen.v.timezone} onChange={(e) => gen.setV({ ...gen.v, timezone: e.target.value })} /></Field>
                <Field label="Daily analysis time"><Input type="time" value={gen.v.daily_analysis_time} onChange={(e) => gen.setV({ ...gen.v, daily_analysis_time: e.target.value })} /></Field>
                <Num label="Planning horizon (days)" value={sch.v.horizon_days} onChange={(x: any) => sch.setV({ ...sch.v, horizon_days: x })} />
                <Num label="Daily generation limit" value={sch.v.daily_generation_limit} onChange={(x: any) => sch.setV({ ...sch.v, daily_generation_limit: x })} />
                <Num label="Max jobs per day" value={sch.v.max_jobs_per_day} onChange={(x: any) => sch.setV({ ...sch.v, max_jobs_per_day: x })} />
                <Num label="Video reuse window (h)" value={sch.v.reuse_window_hours} onChange={(x: any) => sch.setV({ ...sch.v, reuse_window_hours: x })} />
                <Num label="Retry backoff base (s)" value={sch.v.retry_base_seconds} onChange={(x: any) => sch.setV({ ...sch.v, retry_base_seconds: x })} />
                <Num label="Retry backoff max (s)" value={sch.v.retry_max_seconds} onChange={(x: any) => sch.setV({ ...sch.v, retry_max_seconds: x })} />
              </div>
              <Switch checked={sch.v.auto_analyze} onChange={(x) => sch.setV({ ...sch.v, auto_analyze: x })} label="Analyze applications daily" />
              <p className="text-xs text-muted-foreground">Timezone and analysis time apply after a restart.</p>
              <Button onClick={async () => { await gen.save.mutateAsync(gen.v); await sch.save.mutateAsync(sch.v); }}>Save</Button>
            </CardContent>
          </Card>
          <Card>
            <CardHeader><CardTitle>Repetition control & notifications</CardTitle></CardHeader>
            <CardContent className="space-y-4">
              <div className="grid grid-cols-3 gap-4">
                <Num label="Similarity threshold" step={0.05} value={dd.v?.similarity_threshold} onChange={(x: any) => dd.setV({ ...dd.v, similarity_threshold: x })} />
                <Num label="Lookback (days)" value={dd.v?.lookback_days} onChange={(x: any) => dd.setV({ ...dd.v, lookback_days: x })} />
                <Num label="Topic cool-down (days)" value={dd.v?.topic_cooldown_days} onChange={(x: any) => dd.setV({ ...dd.v, topic_cooldown_days: x })} />
              </div>
              <Button size="sm" variant="outline" onClick={() => dd.save.mutate(undefined)}>Save repetition settings</Button>
              <Field label="Alert webhook (Slack / Discord / Teams incoming webhook)" hint="Failures, budget limits and reauthorization requests are posted here as well as in the dashboard.">
                <Input value={notif.v?.webhook_url ?? ""} onChange={(e) => notif.setV({ ...notif.v, webhook_url: e.target.value })} placeholder="https://hooks.slack.com/…" />
              </Field>
              <Button size="sm" variant="outline" onClick={() => notif.save.mutate(undefined)}>Save notifications</Button>
            </CardContent>
          </Card>
        </>}

        {tab === "security" && sec.v && <>
          <Card>
            <CardHeader><div><CardTitle>Allowed project folders</CardTitle><CardDescription>Project sources can only be read from inside these folders.</CardDescription></div></CardHeader>
            <CardContent className="space-y-3">
              {sec.v.allowed_project_roots.map((r: string) => (
                <div key={r} className="flex items-center justify-between rounded-lg border px-3 py-2 font-mono text-xs">{r}
                  <Button size="icon" variant="ghost" onClick={() => sec.save.mutate({ allowed_project_roots: sec.v.allowed_project_roots.filter((x: string) => x !== r) })}><Trash2 /></Button></div>
              ))}
              <div className="flex gap-2">
                <Input value={newRoot} onChange={(e) => setNewRoot(e.target.value)} placeholder="D:\Code" />
                <Button onClick={async () => { await sec.save.mutateAsync({ allowed_project_roots: [...sec.v.allowed_project_roots, newRoot] }); setNewRoot(""); }} disabled={!newRoot}><Plus /> Add</Button>
              </div>
            </CardContent>
          </Card>
          <Card>
            <CardHeader><CardTitle>Change password</CardTitle></CardHeader>
            <CardContent className="space-y-4">
              <Field label="Current password"><Input type="password" value={pw.current_password} onChange={(e) => setPw({ ...pw, current_password: e.target.value })} /></Field>
              <Field label="New password (10+ characters)"><Input type="password" value={pw.new_password} onChange={(e) => setPw({ ...pw, new_password: e.target.value })} /></Field>
              <Button onClick={async () => { await changePw.mutateAsync(); setPw({ current_password: "", new_password: "" }); }} loading={changePw.isPending}>Change password</Button>
            </CardContent>
          </Card>
        </>}

        {tab === "system" && <>
          <Card>
            <CardHeader><CardTitle>Dependency status</CardTitle></CardHeader>
            <CardContent><StatusChecks deep /></CardContent>
          </Card>
          <Card>
            <CardHeader><div><CardTitle>Backups</CardTitle><CardDescription>The database is backed up daily (14 kept). Media files live in the storage folder.</CardDescription></div></CardHeader>
            <CardContent className="space-y-3">
              <Button onClick={() => backup.mutate()} loading={backup.isPending}>Back up now</Button>
              {(backups ?? []).map((b) => <div key={b.name} className="flex justify-between text-xs"><span className="font-mono">{b.name}</span><Badge>{(b.size / 1e6).toFixed(1)} MB</Badge></div>)}
              <p className="text-xs text-muted-foreground">To restore: stop the app, then run scripts\restore.ps1 -Backup &lt;file&gt;.</p>
            </CardContent>
          </Card>
        </>}
      </div>
    </div>
  );
}
