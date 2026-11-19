import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { AppWindow, Plus } from "lucide-react";
import { api } from "@/lib/api";
import { useAction, useApi } from "@/lib/hooks";
import { fmtRelative } from "@/lib/utils";
import { Badge, Button, Card, Dialog, Empty, ErrorBox, Field, Input, PageHeader, Spinner, TagInput, Textarea } from "@/components/ui";

export function AppForm({ initial, onSubmit, busy, error, submitLabel }: {
  initial?: any; onSubmit: (v: any) => void; busy?: boolean; error?: unknown; submitLabel: string;
}) {
  const [v, setV] = useState<any>({
    name: "", description: "", category: "", website_url: "", target_audience: "", main_features: [], selling_points: [],
    tone: "friendly, confident", brand_colors: ["#4F46E5", "#0EA5E9"], language: "en", target_countries: [], cta_text: "",
    ...(initial ?? {}),
  });
  const set = (k: string, val: any) => setV((o: any) => ({ ...o, [k]: val }));
  return (
    <form
      className="space-y-4"
      onSubmit={(e) => {
        e.preventDefault();
        onSubmit({ ...v, website_url: v.website_url || null });
      }}
    >
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Application name"><Input value={v.name} onChange={(e) => set("name", e.target.value)} required /></Field>
        <Field label="Category"><Input value={v.category} onChange={(e) => set("category", e.target.value)} placeholder="SaaS, mobile app, AI platform…" /></Field>
      </div>
      <Field label="Description"><Textarea value={v.description} onChange={(e) => set("description", e.target.value)} rows={3} /></Field>
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Website URL"><Input value={v.website_url ?? ""} onChange={(e) => set("website_url", e.target.value)} placeholder="https://" /></Field>
        <Field label="Target audience"><Input value={v.target_audience} onChange={(e) => set("target_audience", e.target.value)} /></Field>
      </div>
      <Field label="Main features" hint="Verified, released features. These become authoritative facts."><TagInput value={v.main_features} onChange={(x) => set("main_features", x)} /></Field>
      <Field label="Key selling points"><TagInput value={v.selling_points} onChange={(x) => set("selling_points", x)} /></Field>
      <div className="grid gap-4 sm:grid-cols-3">
        <Field label="Marketing tone"><Input value={v.tone} onChange={(e) => set("tone", e.target.value)} /></Field>
        <Field label="Language"><Input value={v.language} onChange={(e) => set("language", e.target.value)} /></Field>
        <Field label="Call to action"><Input value={v.cta_text} onChange={(e) => set("cta_text", e.target.value)} placeholder="Try it free today" /></Field>
      </div>
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Brand colors">
          <div className="flex gap-2">
            {[0, 1].map((i) => (
              <input key={i} type="color" value={v.brand_colors[i] ?? "#4F46E5"} className="h-9 w-16 cursor-pointer rounded-lg border bg-card"
                onChange={(e) => {
                  const c = [...v.brand_colors];
                  c[i] = e.target.value.toUpperCase();
                  set("brand_colors", c);
                }} />
            ))}
          </div>
        </Field>
        <Field label="Target countries"><TagInput value={v.target_countries} onChange={(x) => set("target_countries", x)} placeholder="US, PK, DE…" /></Field>
      </div>
      <ErrorBox error={error} />
      <Button type="submit" loading={busy}>{submitLabel}</Button>
    </form>
  );
}

export function Applications() {
  const { data, isLoading } = useApi<any[]>(["applications"], "/api/applications");
  const [open, setOpen] = useState(false);
  const navigate = useNavigate();
  const create = useAction((v: any) => api.post("/api/applications", v), [["applications"]], "Application created");
  return (
    <div>
      <PageHeader title="Applications" description="Every product you promote, with its sources, knowledge profile and strategy."
        actions={<Button onClick={() => setOpen(true)}><Plus /> Add application</Button>} />
      {isLoading ? <Spinner /> : !data?.length ? (
        <Empty icon={AppWindow} title="No applications yet">Add an application, connect its repository, then analyze it.</Empty>
      ) : (
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {data.map((a) => (
            <Link key={a.id} to={`/applications/${a.id}`}>
              <Card className="h-full p-5 transition-shadow hover:shadow-md">
                <div className="flex items-start justify-between gap-3">
                  <div className="flex items-center gap-3">
                    <div className="size-10 rounded-lg" style={{ background: `linear-gradient(135deg, ${a.brand_colors?.[0] ?? "#6366F1"}, ${a.brand_colors?.[1] ?? "#06B6D4"})` }} />
                    <div>
                      <p className="font-semibold">{a.name}</p>
                      <p className="text-xs text-muted-foreground">{a.category || "Uncategorized"}</p>
                    </div>
                  </div>
                  {!a.active ? <Badge>inactive</Badge> : a.paused ? <Badge tone="warning">paused</Badge> : <Badge tone="success">active</Badge>}
                </div>
                <p className="mt-3 line-clamp-2 text-sm text-muted-foreground">{a.description || "No description"}</p>
                <div className="mt-4 flex flex-wrap gap-2 text-xs">
                  <Badge>{a.sources} sources</Badge>
                  <Badge>{a.accounts} accounts</Badge>
                  <Badge>{a.videos} videos</Badge>
                  <Badge tone={a.knowledge ? "primary" : "warning"}>
                    {a.knowledge ? `${a.knowledge.facts} facts · v${a.knowledge.version}` : "not analyzed"}
                  </Badge>
                  <Badge>{a.publishing_mode}</Badge>
                </div>
                {a.knowledge?.updated_at && <p className="mt-3 text-[11px] text-muted-foreground">Profile updated {fmtRelative(a.knowledge.updated_at)}</p>}
              </Card>
            </Link>
          ))}
        </div>
      )}
      <Dialog open={open} onClose={() => setOpen(false)} title="Add application" wide>
        <AppForm submitLabel="Create application" busy={create.isPending} error={create.error}
          onSubmit={async (v) => {
            const a = await create.mutateAsync(v);
            setOpen(false);
            navigate(`/applications/${a.id}`);
          }} />
      </Dialog>
    </div>
  );
}
