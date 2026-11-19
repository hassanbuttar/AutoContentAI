import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, CheckCircle2, RefreshCw, XCircle } from "lucide-react";
import { api } from "@/lib/api";
import { useAction, useApi } from "@/lib/hooks";
import { Button, Card, CardContent, CardDescription, CardHeader, CardTitle, Field, Input, PageHeader, Spinner } from "@/components/ui";

const NAMES: Record<string, string> = {
  backend: "Backend", database: "Database", claude: "Claude API", comfyui: "ComfyUI", ffmpeg: "FFmpeg", gpu: "GPU",
  storage: "Output directories", project_folders: "Allowed project folders", oauth: "Social OAuth configuration",
};

export function StatusChecks({ deep = false }: { deep?: boolean }) {
  const { data, isFetching, refetch } = useApi(["system-status", deep], `/api/system/status${deep ? "?deep=true" : ""}`);
  if (!data) return <Spinner />;
  return (
    <div className="space-y-2">
      <div className="flex justify-end">
        <Button size="sm" variant="outline" onClick={() => refetch()} loading={isFetching}>
          <RefreshCw /> Re-check
        </Button>
      </div>
      {data.checks.map((c: any) => (
        <div key={c.name} className="flex items-start gap-3 rounded-lg border p-3">
          {c.ok && c.level !== "warning" ? (
            <CheckCircle2 className="mt-0.5 size-4 text-success" />
          ) : c.level === "warning" ? (
            <AlertTriangle className="mt-0.5 size-4 text-warning" />
          ) : (
            <XCircle className="mt-0.5 size-4 text-danger" />
          )}
          <div className="min-w-0 flex-1">
            <p className="text-sm font-medium">{NAMES[c.name] ?? c.name}</p>
            <p className="break-words text-xs text-muted-foreground">{c.detail}</p>
            {c.action && !(c.ok && c.level === "ok") && <p className="mt-1 text-xs text-primary">→ {c.action}</p>}
          </div>
        </div>
      ))}
    </div>
  );
}

export function SetupWizard() {
  const navigate = useNavigate();
  const qc = useQueryClient();
  const [root, setRoot] = useState("");
  const [key, setKey] = useState("");
  const [comfy, setComfy] = useState("http://127.0.0.1:8188");
  const [tz, setTz] = useState(Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC");
  const save = useAction(async () => {
    if (root.trim()) await api.patch("/api/settings/security", { allowed_project_roots: [root.trim()] });
    if (key.trim()) await api.put("/api/secrets", { name: "anthropic.api_key", value: key.trim() });
    await api.patch("/api/settings/comfyui", { url: comfy.trim() });
    await api.patch("/api/settings/general", { timezone: tz });
  }, [["system-status"], ["system-status-light"]], "Saved");
  const finish = useAction(() => api.post("/api/system/setup-complete"), [], "Setup complete");
  return (
    <div>
      <PageHeader title="First-run setup" description="Check dependencies and configure the essentials. Everything can be changed later in Settings." />
      <div className="grid gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <div>
              <CardTitle>1 · System checks</CardTitle>
              <CardDescription>Backend, database, Claude, ComfyUI, FFmpeg, GPU, storage and OAuth.</CardDescription>
            </div>
          </CardHeader>
          <CardContent>
            <StatusChecks deep />
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <div>
              <CardTitle>2 · Essentials</CardTitle>
              <CardDescription>Only folders you list here can be read as project sources.</CardDescription>
            </div>
          </CardHeader>
          <CardContent className="space-y-4">
            <Field label="Allowed project folder" hint="Absolute path, e.g. D:\Code. Project sources must live inside it.">
              <Input value={root} onChange={(e) => setRoot(e.target.value)} placeholder="D:\Code" />
            </Field>
            <Field label="Anthropic API key (optional)" hint="Stored encrypted; never shown again. Without a key the local template planner is used.">
              <Input type="password" value={key} onChange={(e) => setKey(e.target.value)} placeholder="sk-ant-…" autoComplete="off" />
            </Field>
            <Field label="ComfyUI URL">
              <Input value={comfy} onChange={(e) => setComfy(e.target.value)} />
            </Field>
            <Field label="Timezone (IANA)">
              <Input value={tz} onChange={(e) => setTz(e.target.value)} />
            </Field>
            <div className="flex gap-2">
              <Button onClick={() => save.mutate()} loading={save.isPending}>Save & re-check</Button>
              <Button
                variant="success"
                loading={finish.isPending}
                onClick={async () => {
                  await finish.mutateAsync();
                  await qc.invalidateQueries({ queryKey: ["system-status-light"] });
                  navigate("/applications");
                }}
              >
                Finish setup
              </Button>
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
