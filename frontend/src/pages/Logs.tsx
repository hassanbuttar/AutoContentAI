import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Ban, RotateCcw } from "lucide-react";
import { api } from "@/lib/api";
import { useAction, useApi } from "@/lib/hooks";
import { fmtDate, fmtRelative, label } from "@/lib/utils";
import { Button, Card, Dialog, Empty, PageHeader, Progress, Select, Spinner, StatusBadge, Table, Tabs, Td, Th } from "@/components/ui";

function JobDetail({ id }: { id: number }) {
  const { data: j } = useApi(["job", id], `/api/jobs/${id}`, { refetchInterval: 2500 });
  const cancel = useAction(() => api.post(`/api/jobs/${id}/cancel`), [["job", id], ["jobs"]], "Cancel requested");
  const retry = useAction(() => api.post(`/api/jobs/${id}/retry`), [["job", id], ["jobs"]], "Job re-queued");
  if (!j) return <Spinner />;
  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center gap-3">
        <StatusBadge status={j.status} />
        <span className="text-sm text-muted-foreground">attempt {j.attempts}/{j.max_attempts} · lane {j.lane} · run at {fmtDate(j.run_at)}</span>
        <div className="ml-auto flex gap-2">
          {["queued", "running", "retry_wait", "blocked"].includes(j.status) && <Button size="sm" variant="outline" onClick={() => cancel.mutate()}><Ban /> Cancel</Button>}
          {["failed", "cancelled", "retry_wait", "blocked"].includes(j.status) && <Button size="sm" onClick={() => retry.mutate()}><RotateCcw /> Retry now</Button>}
        </div>
      </div>
      {j.status === "running" && <div><p className="mb-1 text-xs text-muted-foreground">{j.progress_message}</p><Progress value={j.progress} /></div>}
      {j.blocked_reason && <p className="rounded-lg bg-warning/10 p-3 text-sm text-warning">{j.blocked_reason}</p>}
      {j.last_error && <p className="rounded-lg bg-danger/10 p-3 text-sm text-danger">{j.last_error}</p>}
      <div>
        <p className="mb-2 text-xs font-medium text-muted-foreground">Log</p>
        <div className="max-h-72 overflow-y-auto rounded-lg border bg-muted/30 p-3 font-mono text-xs scrollbar-thin">
          {j.logs.length === 0 && <p className="text-muted-foreground">No log entries.</p>}
          {j.logs.map((l: any, i: number) => (
            <p key={i} className={l.level === "error" ? "text-danger" : l.level === "warning" ? "text-warning" : ""}>
              <span className="text-muted-foreground">{new Date(l.created_at).toLocaleTimeString()}</span> {l.message}
            </p>
          ))}
        </div>
      </div>
      <div>
        <p className="mb-2 text-xs font-medium text-muted-foreground">Attempts</p>
        {j.attempts_detail.map((a: any) => (
          <details key={a.attempt_no} className="mb-2 rounded-lg border p-2 text-xs">
            <summary className="cursor-pointer">#{a.attempt_no} · {a.status} · {a.duration_s ? `${a.duration_s.toFixed(1)}s` : "—"} · {a.worker_id}{a.error ? ` · ${a.error.slice(0, 120)}` : ""}</summary>
            {a.traceback && <pre className="mt-2 max-h-60 overflow-auto whitespace-pre-wrap text-[11px] text-muted-foreground">{a.traceback}</pre>}
          </details>
        ))}
      </div>
      {j.result && <div><p className="mb-1 text-xs font-medium text-muted-foreground">Result</p><pre className="max-h-48 overflow-auto rounded-lg bg-muted/40 p-3 text-xs">{JSON.stringify(j.result, null, 2)}</pre></div>}
      {j.children?.length > 0 && <p className="text-xs text-muted-foreground">Spawned: {j.children.map((c: any) => `${label(c.job_type)} #${c.id} (${c.status})`).join(", ")}</p>}
    </div>
  );
}

export function Logs() {
  const [params] = useSearchParams();
  const [tab, setTab] = useState("jobs");
  const [status, setStatus] = useState("");
  const [type, setType] = useState("");
  const [open, setOpen] = useState<number | null>(null);
  const jobsUrl = `/api/jobs?limit=150${status ? `&status=${status}` : ""}${type ? `&job_type=${type}` : ""}`;
  const { data: jobs, isLoading } = useApi<any[]>(["jobs", jobsUrl], jobsUrl, { refetchInterval: 3000 });
  const { data: logs } = useApi<any[]>(["logs"], "/api/logs?limit=300", { refetchInterval: 5000, enabled: tab === "log" });
  useEffect(() => { const j = params.get("job"); if (j) setOpen(Number(j)); }, [params]);
  return (
    <div>
      <PageHeader title="Execution Logs" description="Every job, attempt and log line. Failed jobs are never silently discarded." />
      <Tabs value={tab} onChange={setTab} tabs={[{ id: "jobs", label: "Jobs" }, { id: "log", label: "Log stream" }]} />
      <div className="mt-4">
        {tab === "jobs" ? (
          <>
            <div className="mb-3 flex gap-2">
              <Select className="w-48" value={status} onChange={(e) => setStatus(e.target.value)}>
                <option value="">All statuses</option>{["queued", "running", "retry_wait", "blocked", "succeeded", "failed", "cancelled"].map((s) => <option key={s} value={s}>{label(s)}</option>)}
              </Select>
              <Select className="w-56" value={type} onChange={(e) => setType(e.target.value)}>
                <option value="">All job types</option>{["analyze_application", "prepare_content", "plan_content", "render_video", "publish", "verify_publication", "fetch_metrics", "refresh_tokens", "backup"].map((s) => <option key={s} value={s}>{label(s)}</option>)}
              </Select>
            </div>
            {isLoading ? <Spinner /> : !jobs?.length ? <Empty title="No jobs" /> : (
              <Card>
                <Table>
                  <thead><tr><Th>Job</Th><Th>Status</Th><Th>Progress</Th><Th>Attempts</Th><Th>Created</Th><Th>Finished</Th></tr></thead>
                  <tbody>
                    {jobs.map((j) => (
                      <tr key={j.id} onClick={() => setOpen(j.id)} className="cursor-pointer hover:bg-muted/40">
                        <Td><p className="font-medium">{label(j.job_type)}</p><p className="text-xs text-muted-foreground">#{j.id}{j.application_id ? ` · app ${j.application_id}` : ""}</p></Td>
                        <Td><StatusBadge status={j.status} /></Td>
                        <Td className="max-w-sm"><p className="truncate text-xs text-muted-foreground">{j.blocked_reason || j.last_error || j.progress_message}</p>
                          {j.status === "running" && <Progress value={j.progress} className="mt-1" />}</Td>
                        <Td className="text-xs">{j.attempts}/{j.max_attempts}</Td>
                        <Td className="text-xs text-muted-foreground">{fmtRelative(j.created_at)}</Td>
                        <Td className="text-xs text-muted-foreground">{j.finished_at ? fmtRelative(j.finished_at) : "—"}</Td>
                      </tr>
                    ))}
                  </tbody>
                </Table>
              </Card>
            )}
          </>
        ) : (
          <Card className="max-h-[70vh] overflow-y-auto p-4 font-mono text-xs scrollbar-thin">
            {(logs ?? []).map((l) => (
              <p key={l.id} className={`py-0.5 ${l.level === "error" ? "text-danger" : l.level === "warning" ? "text-warning" : ""}`}>
                <span className="text-muted-foreground">{fmtDate(l.created_at, { second: "2-digit" })}</span>{" "}
                {l.job_id && <button className="text-primary" onClick={() => setOpen(l.job_id)}>[job {l.job_id}]</button>} {l.message}
              </p>
            ))}
          </Card>
        )}
      </div>
      <Dialog open={open !== null} onClose={() => setOpen(null)} title={`Job #${open ?? ""}`} wide>{open !== null && <JobDetail id={open} />}</Dialog>
    </div>
  );
}
