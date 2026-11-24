import { useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { AlertTriangle, CheckCircle2, Clapperboard, Download, Send, ThumbsDown, ThumbsUp, Trash2, Upload, XCircle } from "lucide-react";
import { api, ApiError } from "@/lib/api";
import { useAction, useApi, useToast } from "@/lib/hooks";
import { fmtBytes, fmtRelative, PLATFORM_LABEL } from "@/lib/utils";
import {
  Badge, Button, Card, Dialog, Empty, Field, Input, PageHeader, Select, Spinner, StatusBadge, Switch, Tabs, Textarea,
} from "@/components/ui";
import { StoryboardEditor } from "./Studio";

// Platforms a video can go to, from the server's per-platform size/length limits (null = unknown, assume all).
const fitsOn = (v: any, a: any) =>
  a.platform === "export" || a.status === "export_only" || !v.platforms || v.platforms[a.platform]?.ok !== false;
const onlyOn = (v: any): string | null => {
  const p = v.platforms ? Object.values(v.platforms) as any[] : [];
  const ok = p.filter((x) => x.ok).map((x) => x.label);
  return p.length && ok.length < p.length ? (ok.length ? `${ok.join(", ")} only` : "no platform fits") : null;
};

const splitTags = (s: string) =>
  s.split(/[\s,]+/).filter((x) => x.replace(/#/g, "")).map((x) => (x.startsWith("#") ? x : `#${x}`));

function ImportDialog({ open, onClose, apps, defaultApp, onImported }: {
  open: boolean; onClose: () => void; apps: any[]; defaultApp: string; onImported: (id: number) => void;
}) {
  const { toast } = useToast();
  const qc = useQueryClient();
  const input = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [f, setF] = useState({ app: "", title: "", caption: "", hashtags: "", youtube_title: "", fit: "auto", ai: false });
  useEffect(() => {
    if (open) setF((x) => ({ ...x, app: defaultApp || x.app || String(apps[0]?.id ?? "") }));
  }, [open, defaultApp, apps]);
  const pick = (fl: FileList | null) => {
    const x = fl?.[0];
    if (!x) return;
    setFile(x);
    setF((s) => (s.title ? s : { ...s, title: x.name.replace(/\.[^.]+$/, "").replace(/[_-]+/g, " ") }));
  };
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!file || !f.app) return;
    const fd = new FormData();
    fd.append("file", file);
    (["title", "caption", "hashtags", "youtube_title", "fit"] as const).forEach((k) => fd.append(k, f[k]));
    fd.append("ai_generated", String(f.ai));
    setBusy(true);
    try {
      const v: any = await api.upload(`/api/applications/${f.app}/videos/import`, fd);
      const ok = v.status === "ready";
      const replaced = v.replaced_video_id ? ` (replaced the earlier failed/rejected copy #${v.replaced_video_id})` : "";
      toast(ok ? `Video imported${replaced}` : `Imported${replaced}, but it failed QC: see Quality checks`, ok ? "success" : "error");
      qc.invalidateQueries({ queryKey: ["videos"] });
      setFile(null);
      setF((s) => ({ ...s, title: "", caption: "", hashtags: "", youtube_title: "" }));
      onClose();
      onImported(v.id);
    } catch (err: any) {
      if (err instanceof ApiError && err.status === 409 && err.data?.video_id) {
        // Same video already in the library and usable: take the user to it instead of a dead end.
        toast(err.message, "info");
        qc.invalidateQueries({ queryKey: ["videos"] });
        setFile(null);
        onClose();
        onImported(err.data.video_id);
      } else {
        toast(err.message, "error");
      }
    } finally {
      setBusy(false);
    }
  };
  return (
    <Dialog open={open} onClose={() => !busy && onClose()} title="Import a video"
      description="Bring in a finished video made by another script or editor. It is checked and, if needed, converted to 1080x1920 H.264/AAC for Reels, Shorts and TikTok.">
      <form className="space-y-4" onSubmit={submit}>
        <div onDragOver={(e) => e.preventDefault()} onDrop={(e) => { e.preventDefault(); pick(e.dataTransfer.files); }}
          onClick={() => input.current?.click()}
          className="flex cursor-pointer flex-col items-center justify-center rounded-xl border-2 border-dashed p-6 text-center hover:bg-muted/50">
          <Upload className="size-6 text-muted-foreground" />
          <p className="mt-2 text-sm font-medium">{file ? file.name : "Drop a video here or click to choose"}</p>
          <p className="text-xs text-muted-foreground">{file ? fmtBytes(file.size) : "MP4, MOV, WebM, MKV · up to 1 GB. Instagram takes up to 300 MB and 15 min, TikTok up to 10 min (3 min for most accounts), YouTube larger and longer. The quality check shows where it will go."}</p>
          <input ref={input} type="file" accept="video/*,.mkv" hidden onChange={(e) => pick(e.target.files)} />
        </div>
        <Field label="Application">
          <Select value={f.app} onChange={(e) => setF({ ...f, app: e.target.value })} required>
            {apps.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
          </Select>
        </Field>
        <Field label="Title"><Input value={f.title} maxLength={300} onChange={(e) => setF({ ...f, title: e.target.value })} /></Field>
        <Field label="Caption" hint="Used for Instagram and TikTok, and as the YouTube description. Not fact-checked: review it yourself.">
          <Textarea rows={4} maxLength={2200} value={f.caption} onChange={(e) => setF({ ...f, caption: e.target.value })} />
        </Field>
        <Field label="Hashtags" hint="Separated by spaces or commas">
          <Input value={f.hashtags} onChange={(e) => setF({ ...f, hashtags: e.target.value })} />
        </Field>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="YouTube title (optional)">
            <Input value={f.youtube_title} maxLength={100} onChange={(e) => setF({ ...f, youtube_title: e.target.value })} />
          </Field>
          <Field label="If not 9:16" hint="Fit keeps the whole frame on a blurred background">
            <Select value={f.fit} onChange={(e) => setF({ ...f, fit: e.target.value })}>
              <option value="auto">Fit (blurred background)</option>
              <option value="crop">Crop to fill</option>
            </Select>
          </Field>
        </div>
        <Switch checked={f.ai} onChange={(x) => setF({ ...f, ai: x })}
          label="Contains realistic AI-generated people or scenes (sets the AI label on Instagram, TikTok and YouTube)" />
        <Button type="submit" disabled={!file || !f.app} loading={busy}>{busy ? "Importing and checking…" : "Import video"}</Button>
      </form>
    </Dialog>
  );
}

function CaptionEditor({ script, onSaved }: { script: any; onSaved: () => void }) {
  const sc = script.social_content ?? {};
  const [c, setC] = useState({
    caption: sc.instagram?.caption ?? "",
    hashtags: (sc.instagram?.hashtags ?? []).join(" "),
    youtube_title: sc.youtube?.title ?? "",
  });
  const save = useAction(() => {
    const tags = splitTags(c.hashtags);
    return api.put(`/api/scripts/${script.id}`, {
      social_content: {
        instagram: { caption: c.caption, hashtags: tags },
        tiktok: { caption: c.caption, hashtags: tags },
        youtube: { title: c.youtube_title, description: c.caption, tags: tags.map((x) => x.slice(1)) },
      },
    });
  }, [["videos"]], "Captions saved");
  return (
    <div className="space-y-4">
      <p className="text-xs text-muted-foreground">
        Imported video. These captions apply to posts created from now on; posts already in the queue keep their own caption.
      </p>
      <Field label="Caption"><Textarea rows={5} maxLength={2200} value={c.caption} onChange={(e) => setC({ ...c, caption: e.target.value })} /></Field>
      <Field label="Hashtags"><Input value={c.hashtags} onChange={(e) => setC({ ...c, hashtags: e.target.value })} /></Field>
      <Field label="YouTube title"><Input value={c.youtube_title} maxLength={100} onChange={(e) => setC({ ...c, youtube_title: e.target.value })} /></Field>
      <Button loading={save.isPending} onClick={async () => { await save.mutateAsync(undefined); onSaved(); }}>Save captions</Button>
    </div>
  );
}

function VideoDetail({ id, onClose }: { id: number; onClose: () => void }) {
  const { data: v, refetch } = useApi(["video", id], `/api/videos/${id}`);
  const { data: accounts } = useApi<any[]>(["accounts"], "/api/accounts");
  const [tab, setTab] = useState("review");
  const [sched, setSched] = useState({ account: "", when: "", approve: true });
  const inv = [["videos"], ["video", id], ["publications"]];
  const review = useAction((d: string) => api.post(`/api/videos/${id}/review?decision=${d}`), inv, "Review saved");
  const player = useRef<HTMLVideoElement>(null);
  const del = useAction(() => api.del(`/api/videos/${id}`), [["videos"]], "Video deleted");
  const remove = async () => {
    if (!confirm("Delete this video and its file?")) return;
    // Stop streaming first so the browser releases the file (Windows refuses to delete open files).
    const el = player.current;
    if (el) { el.pause(); el.removeAttribute("src"); el.load(); }
    try { await del.mutateAsync(); onClose(); } catch { /* toast shown by useAction */ }
  };
  const schedule = useAction(() => api.post("/api/publications", {
    video_id: id, social_account_id: Number(sched.account), approve: sched.approve,
    scheduled_for: sched.when ? new Date(sched.when).toISOString() : null,
  }), inv, "Publication created");
  if (!v) return <Spinner />;
  const imported = String(v.renderer ?? "").startsWith("imported");
  const accs = (accounts ?? []).filter((a) => a.application_id === v.application_id);
  return (
    <div className="grid gap-6 lg:grid-cols-5">
      <div className="lg:col-span-2">
        {v.url ? <video ref={player} src={v.url} controls autoPlay className="mx-auto aspect-[9/16] w-full max-w-xs rounded-xl bg-black" />
          : <div className="flex aspect-[9/16] items-center justify-center rounded-xl bg-muted">{v.status}</div>}
        <div className="mt-3 flex flex-wrap justify-center gap-2">
          <Button size="sm" variant="success" disabled={v.status !== "ready"} onClick={() => review.mutate("approved")}><ThumbsUp /> Approve</Button>
          <Button size="sm" variant="outline" onClick={() => review.mutate("rejected")}><ThumbsDown /> Reject</Button>
          {v.url && <a href={`${v.url}?download=true`}><Button size="sm" variant="outline"><Download /> Download</Button></a>}
          <Button size="sm" variant="ghost" loading={del.isPending} onClick={remove} aria-label="Delete video"><Trash2 /></Button>
        </div>
        <div className="mt-4 space-y-1 text-xs text-muted-foreground">
          <p>{v.width}×{v.height} · {v.fps} fps · {v.duration}s · {fmtBytes(v.size_bytes)}</p>
          <p>Renderer: {v.renderer} · render {v.render_seconds}s{v.gpu_seconds ? ` · GPU ${v.gpu_seconds}s` : ""}</p>
          <p>Review: <StatusBadge status={v.review_status} /></p>
        </div>
      </div>
      <div className="lg:col-span-3">
        <Tabs value={tab} onChange={setTab} tabs={[{ id: "review", label: "Quality checks" }, { id: "script", label: imported ? "Captions" : "Script & storyboard" },
          { id: "publish", label: "Publish", count: v.publications.length }, { id: "assets", label: "Generation log" }]} />
        <div className="mt-4">
          {tab === "review" && (
            <div className="space-y-2">
              {v.error && <p className="rounded-lg bg-danger/10 p-3 text-sm text-danger">{v.error}</p>}
              {(v.qc_report?.checks ?? []).map((c: any) => (
                <div key={c.name} className="flex items-center gap-2 text-sm">
                  {c.ok
                    ? (c.severity === "notice" ? <AlertTriangle className="size-4 shrink-0 text-warning" /> : <CheckCircle2 className="size-4 shrink-0 text-success" />)
                    : <XCircle className={`size-4 shrink-0 ${c.severity === "warning" ? "text-warning" : "text-danger"}`} />}
                  <span className="w-28 font-medium">{c.name.replace(/_/g, " ")}</span><span className="text-muted-foreground">{c.detail}</span>
                </div>
              ))}
            </div>
          )}
          {tab === "script" && v.script && (imported
            ? <CaptionEditor script={v.script} onSaved={() => refetch()} />
            : <StoryboardEditor script={v.script} onSaved={() => refetch()} />)}
          {tab === "publish" && (
            <div className="space-y-4">
              {v.publications.map((p: any) => (
                <div key={p.id} className="flex items-center justify-between rounded-lg border p-3 text-sm">
                  <span>{PLATFORM_LABEL[p.platform]} · {p.account_name}</span><StatusBadge status={p.status} />
                </div>
              ))}
              <Card className="space-y-3 p-4">
                <p className="text-sm font-semibold">Schedule on an account (manual override)</p>
                <Field label="Account">
                  <Select value={sched.account} onChange={(e) => setSched({ ...sched, account: e.target.value })}>
                    <option value="">Select…</option>
                    {accs.map((a) => {
                      const fits = fitsOn(v, a);
                      return <option key={a.id} value={a.id} disabled={!fits}>
                        {PLATFORM_LABEL[a.platform]} · {a.display_name}{fits ? "" : ` (won't accept: ${v.platforms[a.platform].problems.join("; ")})`}
                      </option>;
                    })}
                  </Select>
                </Field>
                {onlyOn(v) && <p className="text-xs text-warning">This video fits {onlyOn(v)}; accounts on other platforms are disabled.</p>}
                <Field label="When (empty = now)"><Input type="datetime-local" value={sched.when} onChange={(e) => setSched({ ...sched, when: e.target.value })} /></Field>
                <Switch checked={sched.approve} onChange={(x) => setSched({ ...sched, approve: x })} label="Approve immediately" />
                <Button disabled={!sched.account || v.status !== "ready"} loading={schedule.isPending} onClick={() => schedule.mutate()}><Send /> Add to queue</Button>
              </Card>
            </div>
          )}
          {tab === "assets" && (
            <div className="space-y-1.5 text-xs">
              {v.assets.map((a: any) => (
                <div key={a.id} className="flex items-start gap-2 rounded border p-2">
                  <Badge>scene {a.scene_number}</Badge><Badge tone={a.status === "failed" ? "danger" : "default"}>{a.kind} · {a.source}</Badge>
                  <span className="flex-1 text-muted-foreground">{a.error || a.prompt || ""}</span>
                  {a.gpu_seconds ? <span>{a.gpu_seconds.toFixed(0)}s GPU</span> : null}
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

export function Library() {
  const [params] = useSearchParams();
  const [appId, setAppId] = useState("");
  const [review, setReview] = useState("");
  const [open, setOpen] = useState<number | null>(null);
  const [importOpen, setImportOpen] = useState(false);
  const { data: apps } = useApi<any[]>(["applications"], "/api/applications");
  const url = `/api/videos?limit=120${appId ? `&application_id=${appId}` : ""}${review ? `&review_status=${review}` : ""}`;
  const { data, isLoading } = useApi<any[]>(["videos", url], url, { refetchInterval: 10000 });
  useEffect(() => { const v = params.get("video"); if (v) setOpen(Number(v)); }, [params]);
  return (
    <div>
      <PageHeader title="Content Library" description="Every generated or imported video with its QC report, captions and publishing history."
        actions={<>
          <Button onClick={() => setImportOpen(true)} disabled={!apps?.length}><Upload /> Import video</Button>
          <Select className="w-48" value={appId} onChange={(e) => setAppId(e.target.value)}><option value="">All applications</option>{apps?.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}</Select>
          <Select className="w-40" value={review} onChange={(e) => setReview(e.target.value)}><option value="">Any review</option><option value="pending">Pending</option><option value="approved">Approved</option><option value="rejected">Rejected</option></Select>
        </>} />
      {isLoading ? <Spinner /> : !data?.length ? <Empty icon={Clapperboard} title="No videos yet">Generate one from the AI Content Studio, or import a finished video.</Empty> : (
        <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-5">
          {data.map((v) => (
            <button key={v.id} onClick={() => setOpen(v.id)} className="group text-left">
              <div className="relative aspect-[9/16] overflow-hidden rounded-xl bg-muted">
                {v.thumbnail_url ? <img src={v.thumbnail_url} alt="" className="h-full w-full object-cover transition-transform group-hover:scale-105" />
                  : <div className="flex h-full items-center justify-center text-xs text-muted-foreground">{v.status}</div>}
                <div className="absolute left-2 top-2 flex gap-1"><StatusBadge status={v.status} /></div>
                <div className="absolute bottom-2 right-2"><StatusBadge status={v.review_status} /></div>
              </div>
              <p className="mt-2 line-clamp-1 text-sm font-medium">{v.topic || v.hook}</p>
              <p className="text-xs text-muted-foreground">{v.application_name} · {v.duration ? `${Math.round(v.duration)}s` : "—"} · {fmtRelative(v.created_at)}</p>
              {onlyOn(v) && <p className="text-[11px] font-medium text-warning">{onlyOn(v)}</p>}
              {v.publications.length > 0 && <p className="text-[11px] text-muted-foreground">{v.publications.map((p: any) => `${p.platform}:${p.status}`).join(" · ")}</p>}
            </button>
          ))}
        </div>
      )}
      <ImportDialog open={importOpen} onClose={() => setImportOpen(false)} apps={apps ?? []} defaultApp={appId} onImported={(id) => setOpen(id)} />
      <Dialog open={open !== null} onClose={() => setOpen(null)} title={`Video #${open ?? ""}`} wide>
        {open !== null && <VideoDetail id={open} onClose={() => setOpen(null)} />}
      </Dialog>
    </div>
  );
}
