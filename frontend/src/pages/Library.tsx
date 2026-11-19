import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { CheckCircle2, Clapperboard, Download, Send, ThumbsDown, ThumbsUp, Trash2, XCircle } from "lucide-react";
import { api } from "@/lib/api";
import { useAction, useApi } from "@/lib/hooks";
import { fmtBytes, fmtRelative, PLATFORM_LABEL } from "@/lib/utils";
import {
  Badge, Button, Card, Dialog, Empty, Field, Input, PageHeader, Select, Spinner, StatusBadge, Switch, Tabs,
} from "@/components/ui";
import { StoryboardEditor } from "./Studio";

function VideoDetail({ id, onClose }: { id: number; onClose: () => void }) {
  const { data: v, refetch } = useApi(["video", id], `/api/videos/${id}`);
  const { data: accounts } = useApi<any[]>(["accounts"], "/api/accounts");
  const [tab, setTab] = useState("review");
  const [sched, setSched] = useState({ account: "", when: "", approve: true });
  const inv = [["videos"], ["video", id], ["publications"]];
  const review = useAction((d: string) => api.post(`/api/videos/${id}/review?decision=${d}`), inv, "Review saved");
  const del = useAction(() => api.del(`/api/videos/${id}`), [["videos"]], "Video deleted");
  const schedule = useAction(() => api.post("/api/publications", {
    video_id: id, social_account_id: Number(sched.account), approve: sched.approve,
    scheduled_for: sched.when ? new Date(sched.when).toISOString() : null,
  }), inv, "Publication created");
  if (!v) return <Spinner />;
  const accs = (accounts ?? []).filter((a) => a.application_id === v.application_id);
  return (
    <div className="grid gap-6 lg:grid-cols-5">
      <div className="lg:col-span-2">
        {v.url ? <video src={v.url} controls autoPlay className="mx-auto aspect-[9/16] w-full max-w-xs rounded-xl bg-black" />
          : <div className="flex aspect-[9/16] items-center justify-center rounded-xl bg-muted">{v.status}</div>}
        <div className="mt-3 flex flex-wrap justify-center gap-2">
          <Button size="sm" variant="success" disabled={v.status !== "ready"} onClick={() => review.mutate("approved")}><ThumbsUp /> Approve</Button>
          <Button size="sm" variant="outline" onClick={() => review.mutate("rejected")}><ThumbsDown /> Reject</Button>
          {v.url && <a href={`${v.url}?download=true`}><Button size="sm" variant="outline"><Download /> Download</Button></a>}
          <Button size="sm" variant="ghost" onClick={async () => { if (confirm("Delete this video file?")) { await del.mutateAsync(); onClose(); } }}><Trash2 /></Button>
        </div>
        <div className="mt-4 space-y-1 text-xs text-muted-foreground">
          <p>{v.width}×{v.height} · {v.fps} fps · {v.duration}s · {fmtBytes(v.size_bytes)}</p>
          <p>Renderer: {v.renderer} · render {v.render_seconds}s{v.gpu_seconds ? ` · GPU ${v.gpu_seconds}s` : ""}</p>
          <p>Review: <StatusBadge status={v.review_status} /></p>
        </div>
      </div>
      <div className="lg:col-span-3">
        <Tabs value={tab} onChange={setTab} tabs={[{ id: "review", label: "Quality checks" }, { id: "script", label: "Script & storyboard" },
          { id: "publish", label: "Publish", count: v.publications.length }, { id: "assets", label: "Generation log" }]} />
        <div className="mt-4">
          {tab === "review" && (
            <div className="space-y-2">
              {v.error && <p className="rounded-lg bg-danger/10 p-3 text-sm text-danger">{v.error}</p>}
              {(v.qc_report?.checks ?? []).map((c: any) => (
                <div key={c.name} className="flex items-center gap-2 text-sm">
                  {c.ok ? <CheckCircle2 className="size-4 text-success" /> : <XCircle className={`size-4 ${c.severity === "warning" ? "text-warning" : "text-danger"}`} />}
                  <span className="w-28 font-medium">{c.name.replace(/_/g, " ")}</span><span className="text-muted-foreground">{c.detail}</span>
                </div>
              ))}
            </div>
          )}
          {tab === "script" && v.script && <StoryboardEditor script={v.script} onSaved={() => refetch()} />}
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
                    <option value="">Select…</option>{accs.map((a) => <option key={a.id} value={a.id}>{PLATFORM_LABEL[a.platform]} · {a.display_name}</option>)}
                  </Select>
                </Field>
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
  const { data: apps } = useApi<any[]>(["applications"], "/api/applications");
  const url = `/api/videos?limit=120${appId ? `&application_id=${appId}` : ""}${review ? `&review_status=${review}` : ""}`;
  const { data, isLoading } = useApi<any[]>(["videos", url], url, { refetchInterval: 10000 });
  useEffect(() => { const v = params.get("video"); if (v) setOpen(Number(v)); }, [params]);
  return (
    <div>
      <PageHeader title="Content Library" description="Every rendered video with its QC report, script and publishing history."
        actions={<>
          <Select className="w-48" value={appId} onChange={(e) => setAppId(e.target.value)}><option value="">All applications</option>{apps?.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}</Select>
          <Select className="w-40" value={review} onChange={(e) => setReview(e.target.value)}><option value="">Any review</option><option value="pending">Pending</option><option value="approved">Approved</option><option value="rejected">Rejected</option></Select>
        </>} />
      {isLoading ? <Spinner /> : !data?.length ? <Empty icon={Clapperboard} title="No videos yet">Generate one from the AI Content Studio.</Empty> : (
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
              {v.publications.length > 0 && <p className="text-[11px] text-muted-foreground">{v.publications.map((p: any) => `${p.platform}:${p.status}`).join(" · ")}</p>}
            </button>
          ))}
        </div>
      )}
      <Dialog open={open !== null} onClose={() => setOpen(null)} title={`Video #${open ?? ""}`} wide>
        {open !== null && <VideoDetail id={open} onClose={() => setOpen(null)} />}
      </Dialog>
    </div>
  );
}
