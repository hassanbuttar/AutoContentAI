import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Check, ExternalLink, Pencil, RotateCcw, Send, X } from "lucide-react";
import { api } from "@/lib/api";
import { useAction, useApi } from "@/lib/hooks";
import { cn, fmtDate, fmtRelative, PLATFORM_COLOR, PLATFORM_LABEL } from "@/lib/utils";
import {
  Button, Card, Dialog, Empty, Field, Input, PageHeader, Select, Spinner, StatusBadge, Table, Tabs, Td, Textarea, Th,
} from "@/components/ui";

const FILTERS = [
  { id: "awaiting_approval", label: "Needs approval" },
  { id: "planned,preparing,approved,publishing", label: "Upcoming" },
  { id: "failed,verify_required,missed", label: "Needs attention" },
  { id: "exported", label: "Exported" },
  { id: "published", label: "Published" },
  { id: "", label: "All" },
];

function toLocalInput(iso: string) {
  const d = new Date(iso);
  return new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
}

function EditPublication({ pub, onClose }: { pub: any; onClose: () => void }) {
  const [v, setV] = useState({ title: pub.title, caption: pub.caption, hashtags: (pub.hashtags ?? []).join(" "), when: toLocalInput(pub.scheduled_for) });
  const save = useAction(() => api.patch(`/api/publications/${pub.id}`, {
    title: v.title, caption: v.caption, hashtags: v.hashtags.split(/\s+/).filter(Boolean), scheduled_for: new Date(v.when).toISOString(),
  }), [["publications"]], "Publication updated");
  return (
    <div className="space-y-4">
      {pub.rendered_video_id && <video src={`/api/videos/${pub.rendered_video_id}/file`} controls className="mx-auto aspect-[9/16] h-80 rounded-lg bg-black" />}
      {pub.platform === "youtube" && <Field label="Title"><Input value={v.title} maxLength={100} onChange={(e) => setV({ ...v, title: e.target.value })} /></Field>}
      <Field label="Caption"><Textarea rows={4} value={v.caption} onChange={(e) => setV({ ...v, caption: e.target.value })} /></Field>
      <Field label="Hashtags"><Input value={v.hashtags} onChange={(e) => setV({ ...v, hashtags: e.target.value })} /></Field>
      <Field label="Scheduled time (your local time)"><Input type="datetime-local" value={v.when} onChange={(e) => setV({ ...v, when: e.target.value })} /></Field>
      <Button loading={save.isPending} onClick={async () => { await save.mutateAsync(); onClose(); }}>Save</Button>
    </div>
  );
}

export function Queue() {
  const [params] = useSearchParams();
  const [filter, setFilter] = useState("awaiting_approval");
  const [platform, setPlatform] = useState("");
  const [editing, setEditing] = useState<any | null>(null);
  const [markPub, setMarkPub] = useState<any | null>(null);
  const [url, setUrl] = useState("");
  const listUrl = `/api/publications?limit=200${filter ? `&status=${filter}` : ""}${platform ? `&platform=${platform}` : ""}`;
  const { data, isLoading } = useApi<any[]>(["publications", listUrl], listUrl, { refetchInterval: 5000 });
  const inv = [["publications"], ["overview"]];
  const act = useAction(({ id, action, body }: any) => api.post(`/api/publications/${id}/${action}`, body ?? {}), inv, "Done");
  useEffect(() => {
    const p = params.get("pub");
    if (p) api.get(`/api/publications/${p}`).then(setEditing).catch(() => undefined);
  }, [params]);
  return (
    <div>
      <PageHeader title="Publishing Queue" description="Approve, reschedule, publish now, or resolve failed and interrupted posts." />
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <Tabs tabs={FILTERS} value={filter} onChange={setFilter} />
        <Select className="w-48" value={platform} onChange={(e) => setPlatform(e.target.value)}>
          <option value="">All platforms</option>{Object.entries(PLATFORM_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
        </Select>
      </div>
      {isLoading ? <Spinner /> : !data?.length ? <Empty icon={Send} title="Nothing here">Publications appear when schedules prepare content.</Empty> : (
        <Card>
          <Table>
            <thead><tr><Th>Video</Th><Th>Application / account</Th><Th>Scheduled</Th><Th>Status</Th><Th>Caption</Th><Th className="text-right">Actions</Th></tr></thead>
            <tbody>
              {data.map((p) => (
                <tr key={p.id} className="hover:bg-muted/30">
                  <Td>{p.thumbnail_url ? <img src={p.thumbnail_url} className="h-16 w-9 rounded object-cover" alt="" /> : <div className="h-16 w-9 rounded bg-muted" />}</Td>
                  <Td>
                    <p className="font-medium">{p.application_name}</p>
                    <p className="flex items-center gap-1.5 text-xs text-muted-foreground"><span className={cn("size-2 rounded-full", PLATFORM_COLOR[p.platform])} />{p.account_name}</p>
                  </Td>
                  <Td className="whitespace-nowrap text-xs">{fmtDate(p.scheduled_for)}<p className="text-muted-foreground">{fmtRelative(p.scheduled_for)}</p></Td>
                  <Td>
                    <StatusBadge status={p.status} />
                    {p.last_error && <p className="mt-1 max-w-xs text-[11px] text-danger">{p.last_error}</p>}
                    {p.export_path && <p className="mt-1 max-w-xs break-all text-[11px] text-muted-foreground">{p.export_path}</p>}
                    {p.external_url && <a href={p.external_url} target="_blank" rel="noreferrer" className="mt-1 inline-flex items-center gap-1 text-[11px] text-primary">View post <ExternalLink className="size-3" /></a>}
                  </Td>
                  <Td className="max-w-xs"><p className="line-clamp-2 text-xs">{p.caption || "—"}</p></Td>
                  <Td>
                    <div className="flex flex-wrap justify-end gap-1">
                      {["awaiting_approval", "failed", "missed"].includes(p.status) && p.rendered_video_id &&
                        <Button size="sm" variant="success" onClick={() => act.mutate({ id: p.id, action: "approve" })}><Check /> Approve</Button>}
                      {["approved", "awaiting_approval", "failed"].includes(p.status) && p.rendered_video_id &&
                        <Button size="sm" variant="outline" onClick={() => confirm("Publish now?") && act.mutate({ id: p.id, action: "publish-now" })}><Send /> Now</Button>}
                      {["failed", "missed"].includes(p.status) && <Button size="sm" variant="outline" onClick={() => act.mutate({ id: p.id, action: "retry" })}><RotateCcw /> Retry</Button>}
                      {p.status === "verify_required" && <>
                        <Button size="sm" variant="outline" onClick={() => { setMarkPub(p); setUrl(""); }}>It is live</Button>
                        <Button size="sm" variant="outline" onClick={() => confirm("You checked the platform and the post is NOT live?") && act.mutate({ id: p.id, action: "confirm-not-published" })}>Not live</Button>
                      </>}
                      {p.status === "exported" && <Button size="sm" variant="outline" onClick={() => { setMarkPub(p); setUrl(""); }}>Mark published</Button>}
                      {!["published", "publishing", "cancelled", "rejected"].includes(p.status) && <Button size="icon" variant="ghost" onClick={() => setEditing(p)}><Pencil /></Button>}
                      {["awaiting_approval", "approved", "planned"].includes(p.status) && <Button size="icon" variant="ghost" onClick={() => act.mutate({ id: p.id, action: "reject" })}><X /></Button>}
                    </div>
                  </Td>
                </tr>
              ))}
            </tbody>
          </Table>
        </Card>
      )}
      <Dialog open={!!editing} onClose={() => setEditing(null)} title={`Publication #${editing?.id ?? ""}`}>
        {editing && <EditPublication pub={editing} onClose={() => setEditing(null)} />}
      </Dialog>
      <Dialog open={!!markPub} onClose={() => setMarkPub(null)} title="Mark as published" description="Record a post you published manually or verified on the platform.">
        <div className="space-y-4">
          <Field label="Post URL (optional)"><Input value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://" /></Field>
          <Button onClick={async () => { await act.mutateAsync({ id: markPub.id, action: "mark-published", body: { url: url || null } }); setMarkPub(null); }}>Confirm</Button>
        </div>
      </Dialog>
    </div>
  );
}
