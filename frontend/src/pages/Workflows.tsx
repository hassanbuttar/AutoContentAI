import { useRef, useState } from "react";
import { Boxes, CheckCircle2, ShieldCheck, Trash2, Upload, XCircle } from "lucide-react";
import { api } from "@/lib/api";
import { useAction, useApi, useToast } from "@/lib/hooks";
import { fmtRelative } from "@/lib/utils";
import { Badge, Button, Card, CardContent, CardHeader, CardTitle, Dialog, Empty, Field, Input, PageHeader, Select, Spinner, Switch, Textarea } from "@/components/ui";

const KEYS = ["positive_prompt", "negative_prompt", "seed", "width", "height", "length", "fps", "steps", "input_image", "filename_prefix", "batch_size"];

function MappingEditor({ wf, onClose }: { wf: any; onClose: () => void }) {
  const [m, setM] = useState<any>(wf.mappings ?? {});
  const [defaults, setDefaults] = useState(JSON.stringify(wf.defaults ?? {}, null, 2));
  const [timeout, setTimeoutS] = useState(wf.timeout_seconds);
  const save = useAction(() => api.patch(`/api/workflows/${wf.id}`, { mappings: m, defaults: JSON.parse(defaults || "{}"), timeout_seconds: Number(timeout) }),
    [["workflows"]], "Mappings saved — validate again");
  return (
    <div className="space-y-4">
      <p className="text-xs text-muted-foreground">Tell AutoContent which node input receives each value. Only literal inputs are listed (links are excluded).</p>
      <div className="space-y-2">
        {KEYS.map((k) => {
          const cur = m[k];
          const node = wf.nodes.find((n: any) => n.id === cur?.node);
          return (
            <div key={k} className="grid grid-cols-[150px_1fr_1fr_auto] items-center gap-2">
              <span className="font-mono text-xs">{k}</span>
              <Select className="h-8 text-xs" value={cur?.node ?? ""} onChange={(e) => setM({ ...m, [k]: e.target.value ? { node: e.target.value, input: "" } : undefined })}>
                <option value="">— not mapped —</option>
                {wf.nodes.map((n: any) => <option key={n.id} value={n.id}>#{n.id} {n.class_type}</option>)}
              </Select>
              <Select className="h-8 text-xs" value={cur?.input ?? ""} disabled={!node} onChange={(e) => setM({ ...m, [k]: { ...cur, input: e.target.value } })}>
                <option value="">input…</option>{node?.inputs.map((i: string) => <option key={i}>{i}</option>)}
              </Select>
              <button className="text-muted-foreground" onClick={() => { const c = { ...m }; delete c[k]; setM(c); }}>✕</button>
            </div>
          );
        })}
      </div>
      <div className="grid gap-4 sm:grid-cols-3">
        <Field label="Defaults (JSON)" hint="width, height, steps, length, fps, negative_prompt" className="sm:col-span-2">
          <Textarea rows={6} className="font-mono text-xs" value={defaults} onChange={(e) => setDefaults(e.target.value)} />
        </Field>
        <Field label="Timeout (seconds)"><Input type="number" value={timeout} onChange={(e) => setTimeoutS(e.target.value)} /></Field>
      </div>
      <Button loading={save.isPending} onClick={async () => { await save.mutateAsync(); onClose(); }}>Save mappings</Button>
    </div>
  );
}

export function Workflows() {
  const { toast } = useToast();
  const { data: status } = useApi(["comfy-status"], "/api/comfyui/status", { refetchInterval: 10000 });
  const { data, isLoading, refetch } = useApi<any[]>(["workflows"], "/api/workflows");
  const [editing, setEditing] = useState<any | null>(null);
  const [kind, setKind] = useState("txt2img");
  const file = useRef<HTMLInputElement>(null);
  const validate = useAction((id: number) => api.post(`/api/workflows/${id}/validate`), [["workflows"]]);
  const patch = useAction(({ id, ...v }: any) => api.patch(`/api/workflows/${id}`, v), [["workflows"]]);
  const del = useAction((id: number) => api.del(`/api/workflows/${id}`), [["workflows"]], "Workflow removed");
  const importFile = async (f?: File) => {
    if (!f) return;
    const fd = new FormData();
    fd.append("file", f);
    fd.append("kind", kind);
    try {
      const w = await api.upload("/api/workflows/import", fd);
      toast("Imported. Review the suggested mappings, then validate.", "success");
      await refetch();
      setEditing(w);
    } catch (e: any) {
      toast(e.message, "error");
    } finally {
      if (file.current) file.current.value = "";
    }
  };
  return (
    <div>
      <PageHeader title="ComfyUI Workflows" description="Workflows are validated against the nodes and models actually installed on your ComfyUI server."
        actions={<>
          <Select className="w-36" value={kind} onChange={(e) => setKind(e.target.value)}><option value="txt2img">txt2img</option><option value="img2vid">img2vid</option><option value="txt2vid">txt2vid</option></Select>
          <Button onClick={() => file.current?.click()}><Upload /> Import API-format JSON</Button>
          <input ref={file} type="file" accept=".json,application/json" hidden onChange={(e) => importFile(e.target.files?.[0])} />
        </>} />
      <Card className="mb-6 p-4">
        {!status ? <Spinner /> : status.ok ? (
          <div className="flex flex-wrap items-center gap-3 text-sm">
            <CheckCircle2 className="size-4 text-success" /> ComfyUI {status.version} · {status.gpu} · {status.vram_free_gb}/{status.vram_total_gb} GB VRAM free · queue {status.running} running / {status.pending} pending
          </div>
        ) : (
          <div className="flex items-start gap-3 text-sm"><XCircle className="mt-0.5 size-4 text-danger" /><div><p>{status.error}</p><p className="text-xs text-primary">{status.action}</p>
            <p className="text-xs text-muted-foreground">Videos still render with the local fallback (screenshots + title cards).</p></div></div>
        )}
      </Card>
      {isLoading ? <Spinner /> : !data?.length ? <Empty icon={Boxes} title="No workflows">Export a workflow from ComfyUI with Workflow → Export (API) and import it here.</Empty> : (
        <div className="grid gap-4 lg:grid-cols-2">
          {data.map((w) => {
            const rep = w.validation_report ?? {};
            return (
              <Card key={w.id}>
                <CardHeader>
                  <div>
                    <CardTitle>{w.name}</CardTitle>
                    <p className="mt-1 text-xs text-muted-foreground">{w.description}</p>
                  </div>
                  <div className="flex shrink-0 gap-1.5"><Badge tone="primary">{w.kind}</Badge>{w.is_default && <Badge>default</Badge>}</div>
                </CardHeader>
                <CardContent className="space-y-3">
                  <div className="flex flex-wrap gap-2 text-xs">
                    <Badge>{w.node_count} nodes</Badge><Badge>{Object.keys(w.mappings).length} mappings</Badge><Badge>timeout {w.timeout_seconds}s</Badge>
                    {w.validated ? <Badge tone="success">validated {fmtRelative(rep.checked_at)}</Badge> : rep.problems?.length ? <Badge tone="danger">invalid</Badge> : <Badge tone="warning">not validated</Badge>}
                  </div>
                  {rep.problems?.length > 0 && <ul className="space-y-1 rounded-lg bg-danger/10 p-3 text-xs text-danger">{rep.problems.map((p: string) => <li key={p}>• {p}</li>)}</ul>}
                  {rep.warnings?.length > 0 && <ul className="text-xs text-warning">{rep.warnings.map((p: string) => <li key={p}>• {p}</li>)}</ul>}
                  <div className="flex flex-wrap items-center gap-2">
                    <Button size="sm" onClick={() => validate.mutate(w.id)} loading={validate.isPending && validate.variables === w.id}><ShieldCheck /> Validate</Button>
                    <Button size="sm" variant="outline" onClick={() => setEditing(w)}>Edit mappings</Button>
                    <Switch checked={w.enabled} onChange={(v) => patch.mutate({ id: w.id, enabled: v })} label="Enabled" />
                    <Switch checked={w.is_default} onChange={(v) => patch.mutate({ id: w.id, is_default: v })} label="Default" />
                    <Button size="icon" variant="ghost" className="ml-auto" onClick={() => confirm("Delete workflow?") && del.mutate(w.id)}><Trash2 /></Button>
                  </div>
                </CardContent>
              </Card>
            );
          })}
        </div>
      )}
      <Dialog open={!!editing} onClose={() => setEditing(null)} title={`Mappings · ${editing?.name ?? ""}`} wide>
        {editing && <MappingEditor wf={editing} onClose={() => setEditing(null)} />}
      </Dialog>
    </div>
  );
}
