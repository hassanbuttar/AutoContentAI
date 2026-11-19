import { Link } from "react-router-dom";
import { AlertTriangle, AppWindow, CheckCircle2, Clapperboard, Cpu, Send, Timer, Users, XCircle } from "lucide-react";
import { useApi } from "@/lib/hooks";
import { cn, fmtDate, fmtNum, fmtRelative, fmtUsd, label, PLATFORM_COLOR, PLATFORM_LABEL } from "@/lib/utils";
import { Badge, Card, CardContent, CardHeader, CardTitle, Empty, PageHeader, Progress, Spinner, StatusBadge } from "@/components/ui";

function Stat({ label, value, sub, icon: Icon, tone }: { label: string; value: any; sub?: string; icon: any; tone?: string }) {
  return (
    <Card className="p-4">
      <div className="flex items-start justify-between">
        <div>
          <p className="text-xs text-muted-foreground">{label}</p>
          <p className="mt-1 text-2xl font-semibold tracking-tight">{value}</p>
          {sub && <p className="mt-0.5 text-xs text-muted-foreground">{sub}</p>}
        </div>
        <div className={cn("rounded-lg p-2", tone ?? "bg-primary/10 text-primary")}>
          <Icon className="size-4" />
        </div>
      </div>
    </Card>
  );
}

export function Overview() {
  const { data, isLoading } = useApi(["overview"], "/api/system/overview", { refetchInterval: 5000 });
  if (isLoading || !data) return <Spinner />;
  const s = data.stats;
  const comfy = data.worker.comfyui;
  const ai = data.ai;
  return (
    <div>
      <PageHeader title="Overview" description="Everything your local studio is doing right now." />
      <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
        <Stat label="Applications" value={s.applications} sub={`${s.active_applications} active`} icon={AppWindow} />
        <Stat label="Connected accounts" value={s.connected_accounts}
          sub={s.accounts_needing_attention ? `${s.accounts_needing_attention} need attention` : "all healthy"} icon={Users} />
        <Stat label="Scheduled today" value={s.scheduled_today} sub={`${s.awaiting_approval} awaiting approval`} icon={Timer} />
        <Stat label="Videos generated" value={s.videos_generated} sub={`${s.videos_generated_today} today`} icon={Clapperboard} />
        <Stat label="Published" value={s.published} sub={`${s.published_today} today`} icon={Send} tone="bg-success/10 text-success" />
        <Stat label="Failed jobs (7d)" value={s.failed_jobs_7d} sub={`${s.failed_publications} publications need action`}
          icon={XCircle} tone={s.failed_jobs_7d ? "bg-danger/10 text-danger" : "bg-muted text-muted-foreground"} />
        <Stat label="GPU / ComfyUI" value={comfy?.ok ? "Online" : comfy ? "Offline" : "Disabled"}
          sub={comfy?.ok ? `${comfy.vram_free_gb}/${comfy.vram_total_gb} GB free · queue ${comfy.running + comfy.pending}` : "fallback renderer active"}
          icon={Cpu} tone={comfy?.ok ? "bg-success/10 text-success" : "bg-warning/10 text-warning"} />
        <Stat label="Claude this month" value={fmtUsd(ai.cost_month)} sub={`${fmtNum(ai.tokens_month)} tokens · ${ai.calls_today} calls today`}
          icon={ai.exceeded ? AlertTriangle : CheckCircle2} tone={ai.exceeded ? "bg-danger/10 text-danger" : "bg-primary/10 text-primary"} />
      </div>

      {ai.exceeded && (
        <div className="mt-4 rounded-lg border border-warning/40 bg-warning/10 px-4 py-3 text-sm">
          <strong>AI budget reached:</strong> {ai.reasons.join("; ")}. AI-dependent jobs are paused. <Link className="text-primary underline" to="/usage">Review limits</Link>
        </div>
      )}

      <div className="mt-6 grid gap-6 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <CardHeader>
            <CardTitle>Current jobs</CardTitle>
            <Link to="/logs" className="text-xs text-primary">All jobs →</Link>
          </CardHeader>
          <CardContent className="space-y-3">
            {data.jobs.length === 0 && <p className="text-sm text-muted-foreground">Idle. Nothing queued.</p>}
            {data.jobs.map((j: any) => (
              <Link to={`/logs?job=${j.id}`} key={j.id} className="block rounded-lg border p-3 hover:bg-muted/50">
                <div className="flex items-center justify-between gap-2">
                  <span className="text-sm font-medium">{label(j.job_type)} <span className="text-muted-foreground">#{j.id}</span></span>
                  <StatusBadge status={j.status} />
                </div>
                <p className="mt-1 truncate text-xs text-muted-foreground">{j.blocked_reason || j.progress_message || j.last_error || "—"}</p>
                {j.status === "running" && <Progress value={j.progress} className="mt-2" />}
              </Link>
            ))}
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle>Upcoming posts</CardTitle>
            <Link to="/calendar" className="text-xs text-primary">Calendar →</Link>
          </CardHeader>
          <CardContent className="space-y-2">
            {data.upcoming.length === 0 && <Empty title="No upcoming posts">Add publishing rules in the Scheduler.</Empty>}
            {data.upcoming.map((p: any) => (
              <div key={p.id} className="flex items-center gap-3 rounded-lg border p-2.5">
                <span className={cn("size-2 shrink-0 rounded-full", PLATFORM_COLOR[p.platform])} />
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm font-medium">{p.application_name}</p>
                  <p className="text-xs text-muted-foreground">{PLATFORM_LABEL[p.platform]} · {fmtDate(p.scheduled_for)} ({fmtRelative(p.scheduled_for)})</p>
                </div>
                <StatusBadge status={p.status} />
              </div>
            ))}
          </CardContent>
        </Card>
      </div>
      <Card className="mt-6">
        <CardHeader>
          <CardTitle>Per-application output</CardTitle>
          <Link to="/analytics" className="text-xs text-primary">Analytics →</Link>
        </CardHeader>
        <CardContent>
          {data.per_application.length === 0 ? (
            <Empty title="No applications yet"><Link className="text-primary" to="/applications">Register your first application</Link></Empty>
          ) : (
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
              {data.per_application.map((a: any) => (
                <Link key={a.id} to={`/applications/${a.id}`} className="flex items-center justify-between rounded-lg border p-3 hover:bg-muted/50">
                  <span className="font-medium">{a.name}</span>
                  <span className="flex gap-2">
                    <Badge>{a.videos} videos</Badge>
                    <Badge tone="success">{a.published} published</Badge>
                  </span>
                </Link>
              ))}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
