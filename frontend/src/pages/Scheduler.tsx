import { useEffect, useState } from "react";
import { CalendarClock, Pencil, Plus, Trash2, Zap } from "lucide-react";
import { api } from "@/lib/api";
import { useAction, useApi } from "@/lib/hooks";
import { CONTENT_TYPES, cn, fmtDate, label, PLATFORM_COLOR, PLATFORM_LABEL } from "@/lib/utils";
import {
  Badge, Button, Card, Dialog, Empty, ErrorBox, Field, Input, PageHeader, Select, Spinner, Switch, TagInput,
} from "@/components/ui";

const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const DEFAULT_RULE = {
  name: "", enabled: true, frequency: "daily", posts_per_period: 1, days_of_week: [0, 1, 2, 3, 4, 5, 6], posting_times: [] as string[],
  window_start: "09:00", window_end: "21:00", timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC", start_date: "",
  end_date: "", blackout_periods: [] as any[], max_per_day: "", generation_lead_hours: 12, missed_policy: "publish_late",
  grace_minutes: 120, reuse_across_platforms: true, content_types: [] as string[],
};

function RuleForm({ accounts, initial, onDone }: { accounts: any[]; initial?: any; onDone: () => void }) {
  const [r, setR] = useState<any>({ ...DEFAULT_RULE, social_account_id: accounts[0]?.id, ...(initial ?? {}) });
  const [preview, setPreview] = useState<any>(null);
  const [blackStart, setBlackStart] = useState("");
  const [blackEnd, setBlackEnd] = useState("");
  const body = () => ({ ...r, max_per_day: r.max_per_day === "" ? null : Number(r.max_per_day), start_date: r.start_date || null,
    end_date: r.end_date || null, posts_per_period: Number(r.posts_per_period), generation_lead_hours: Number(r.generation_lead_hours),
    grace_minutes: Number(r.grace_minutes), social_account_id: Number(r.social_account_id) });
  const save = useAction(() => (initial?.id ? api.put(`/api/rules/${initial.id}`, body()) : api.post("/api/rules", body())),
    [["rules"], ["calendar"]], "Schedule saved");
  useEffect(() => {
    const t = setTimeout(() => {
      api.post("/api/rules/preview?days=14", body()).then(setPreview).catch((e) => setPreview({ error: e.message }));
    }, 300);
    return () => clearTimeout(t);
  }, [JSON.stringify(r)]); // eslint-disable-line react-hooks/exhaustive-deps
  const set = (k: string, v: any) => setR((o: any) => ({ ...o, [k]: v }));
  return (
    <div className="grid gap-6 lg:grid-cols-5">
      <form className="space-y-4 lg:col-span-3" onSubmit={async (e) => { e.preventDefault(); await save.mutateAsync(); onDone(); }}>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Social account">
            <Select value={r.social_account_id} onChange={(e) => set("social_account_id", e.target.value)}>
              {accounts.map((a) => <option key={a.id} value={a.id}>{a.app_name} · {PLATFORM_LABEL[a.platform]} · {a.display_name}</option>)}
            </Select>
          </Field>
          <Field label="Rule name"><Input value={r.name} onChange={(e) => set("name", e.target.value)} placeholder="Weekday reels" /></Field>
        </div>
        <div className="grid gap-4 sm:grid-cols-3">
          <Field label="Frequency">
            <Select value={r.frequency} onChange={(e) => set("frequency", e.target.value)}><option value="daily">Per day</option><option value="weekly">Per week</option></Select>
          </Field>
          <Field label={r.frequency === "daily" ? "Videos per day" : "Videos per week"}>
            <Input type="number" min={1} max={20} value={r.posts_per_period} onChange={(e) => set("posts_per_period", e.target.value)} />
          </Field>
          <Field label="Max per day (optional)"><Input type="number" min={1} value={r.max_per_day ?? ""} onChange={(e) => set("max_per_day", e.target.value)} /></Field>
        </div>
        <Field label="Posting days">
          <div className="flex flex-wrap gap-1.5">
            {DAYS.map((d, i) => {
              const on = r.days_of_week.includes(i);
              return <button type="button" key={d} onClick={() => set("days_of_week", on ? r.days_of_week.filter((x: number) => x !== i) : [...r.days_of_week, i].sort())}
                className={cn("h-8 w-12 rounded-md border text-xs", on ? "border-primary bg-primary/10 text-primary" : "text-muted-foreground")}>{d}</button>;
            })}
          </div>
        </Field>
        <Field label="Specific posting times (HH:MM)" hint="Leave empty to distribute posts evenly inside the publishing window.">
          <TagInput value={r.posting_times} onChange={(v) => set("posting_times", v.filter((t) => /^([01]\d|2[0-3]):[0-5]\d$/.test(t)))} placeholder="09:00, 18:30…" />
        </Field>
        <div className="grid gap-4 sm:grid-cols-3">
          <Field label="Window start"><Input type="time" value={r.window_start} onChange={(e) => set("window_start", e.target.value)} /></Field>
          <Field label="Window end"><Input type="time" value={r.window_end} onChange={(e) => set("window_end", e.target.value)} /></Field>
          <Field label="Timezone (IANA)"><Input value={r.timezone} onChange={(e) => set("timezone", e.target.value)} /></Field>
        </div>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Campaign start"><Input type="date" value={r.start_date ?? ""} onChange={(e) => set("start_date", e.target.value)} /></Field>
          <Field label="Campaign end"><Input type="date" value={r.end_date ?? ""} onChange={(e) => set("end_date", e.target.value)} /></Field>
        </div>
        <Field label="Blackout periods (local time)">
          <div className="space-y-2">
            {r.blackout_periods.map((b: any, i: number) => (
              <div key={i} className="flex items-center gap-2 text-xs">
                <Badge>{b.start.replace("T", " ")} → {b.end.replace("T", " ")}</Badge>
                <button type="button" onClick={() => set("blackout_periods", r.blackout_periods.filter((_: any, j: number) => j !== i))}><Trash2 className="size-3.5" /></button>
              </div>
            ))}
            <div className="flex gap-2">
              <Input type="datetime-local" value={blackStart} onChange={(e) => setBlackStart(e.target.value)} />
              <Input type="datetime-local" value={blackEnd} onChange={(e) => setBlackEnd(e.target.value)} />
              <Button type="button" variant="outline" onClick={() => { if (blackStart && blackEnd) { set("blackout_periods", [...r.blackout_periods, { start: blackStart, end: blackEnd }]); setBlackStart(""); setBlackEnd(""); } }}>Add</Button>
            </div>
          </div>
        </Field>
        <div className="grid gap-4 sm:grid-cols-3">
          <Field label="Generate ahead (hours)" hint="Content is prepared this long before the slot.">
            <Input type="number" min={1} max={168} value={r.generation_lead_hours} onChange={(e) => set("generation_lead_hours", e.target.value)} />
          </Field>
          <Field label="If the PC was offline">
            <Select value={r.missed_policy} onChange={(e) => set("missed_policy", e.target.value)}>
              <option value="publish_late">Publish late (within grace)</option><option value="skip">Skip the slot</option><option value="next_slot">Move content to next slot</option>
            </Select>
          </Field>
          <Field label="Grace period (minutes)"><Input type="number" min={0} value={r.grace_minutes} onChange={(e) => set("grace_minutes", e.target.value)} /></Field>
        </div>
        <Field label="Content types for this account" hint="Empty = application strategy.">
          <Select multiple className="h-24" value={r.content_types} onChange={(e) => set("content_types", Array.from(e.target.selectedOptions).map((o) => o.value))}>
            {CONTENT_TYPES.map((t) => <option key={t} value={t}>{label(t)}</option>)}
          </Select>
        </Field>
        <div className="flex flex-wrap gap-6">
          <Switch checked={r.reuse_across_platforms} onChange={(v) => set("reuse_across_platforms", v)} label="Reuse one video across platforms" />
          <Switch checked={r.enabled} onChange={(v) => set("enabled", v)} label="Enabled" />
        </div>
        <ErrorBox error={save.error} />
        <Button type="submit" loading={save.isPending}>{initial?.id ? "Save schedule" : "Create schedule"}</Button>
      </form>
      <div className="lg:col-span-2">
        <Card className="sticky top-4 p-4">
          <p className="text-sm font-semibold">Next 14 days</p>
          {preview?.error ? <p className="mt-2 text-xs text-danger">{preview.error}</p> : !preview ? <Spinner /> : (
            <>
              <p className="mb-3 text-xs text-muted-foreground">{preview.count} posts</p>
              <div className="max-h-[480px] space-y-1 overflow-y-auto scrollbar-thin">
                {preview.slots.map((s: string) => <p key={s} className="text-xs tabular-nums">{fmtDate(s, { weekday: "short" })}</p>)}
              </div>
            </>
          )}
        </Card>
      </div>
    </div>
  );
}

export function Scheduler() {
  const { data: accounts } = useApi<any[]>(["accounts"], "/api/accounts");
  const { data: apps } = useApi<any[]>(["applications"], "/api/applications");
  const { data: rules, isLoading } = useApi<any[]>(["rules"], "/api/rules");
  const [editing, setEditing] = useState<any | null>(null);
  const del = useAction((id: number) => api.del(`/api/rules/${id}`), [["rules"]], "Schedule removed");
  const toggle = useAction((r: any) => api.put(`/api/rules/${r.id}`, { ...r, enabled: !r.enabled }), [["rules"]]);
  const pauseApp = useAction((a: any) => api.patch(`/api/applications/${a.id}`, { paused: !a.paused }), [["applications"]], "Updated");
  const tick = useAction(() => api.post("/api/system/tick"), [["rules"], ["overview"]], "Scheduler ran");
  const appName = (id: number) => apps?.find((a) => a.id === id)?.name ?? "";
  const accs = (accounts ?? []).map((a) => ({ ...a, app_name: appName(a.application_id) }));
  return (
    <div>
      <PageHeader title="Automation Scheduler" description="Independent schedules per application and social account. Persisted in the database; restarts never duplicate posts."
        actions={<>
          <Button variant="outline" onClick={() => tick.mutate()} loading={tick.isPending}><Zap /> Run scheduler now</Button>
          <Button onClick={() => setEditing({})} disabled={!accs.length}><Plus /> New schedule</Button>
        </>} />
      {isLoading ? <Spinner /> : !accs.length ? <Empty icon={CalendarClock} title="Add a social account first">Schedules are attached to accounts.</Empty> : (
        <div className="space-y-6">
          {(apps ?? []).map((ap) => {
            const appRules = (rules ?? []).filter((r) => r.application_id === ap.id);
            const appAccs = accs.filter((a) => a.application_id === ap.id);
            if (!appAccs.length) return null;
            return (
              <Card key={ap.id} className="p-5">
                <div className="mb-4 flex items-center justify-between">
                  <div><p className="font-semibold">{ap.name}</p><p className="text-xs text-muted-foreground">{appRules.length} schedule(s)</p></div>
                  <Switch checked={!ap.paused} onChange={() => pauseApp.mutate(ap)} label={ap.paused ? "Application paused" : "Running"} />
                </div>
                {appRules.length === 0 ? <p className="text-sm text-muted-foreground">No schedules for this application.</p> : (
                  <div className="grid gap-3 md:grid-cols-2">
                    {appRules.map((r) => {
                      const acc = accs.find((a) => a.id === r.social_account_id);
                      return (
                        <div key={r.id} className={cn("rounded-lg border p-3", !r.enabled && "opacity-60")}>
                          <div className="flex items-center justify-between gap-2">
                            <div className="flex items-center gap-2">
                              <span className={cn("size-2.5 rounded-full", PLATFORM_COLOR[acc?.platform])} />
                              <span className="text-sm font-medium">{r.name || PLATFORM_LABEL[acc?.platform]}</span>
                            </div>
                            <div className="flex items-center gap-1">
                              <Switch checked={r.enabled} onChange={() => toggle.mutate(r)} />
                              <Button size="icon" variant="ghost" onClick={() => setEditing(r)}><Pencil /></Button>
                              <Button size="icon" variant="ghost" onClick={() => confirm("Delete schedule? Planned (unprepared) slots are cancelled.") && del.mutate(r.id)}><Trash2 /></Button>
                            </div>
                          </div>
                          <p className="mt-2 text-xs text-muted-foreground">
                            {r.posts_per_period}× per {r.frequency === "daily" ? "day" : "week"} · {r.days_of_week.length === 7 ? "every day" : r.days_of_week.map((d: number) => DAYS[d]).join(" ")}
                            {" · "}{r.posting_times.length ? r.posting_times.join(", ") : `${r.window_start}–${r.window_end}`} · {r.timezone}
                          </p>
                          <p className="mt-1 text-[11px] text-muted-foreground">
                            {acc?.display_name} · prepare {r.generation_lead_hours}h ahead · missed: {label(r.missed_policy)}{r.reuse_across_platforms ? " · reuse videos" : ""}
                            {r.start_date && ` · ${r.start_date} → ${r.end_date ?? "∞"}`}
                          </p>
                        </div>
                      );
                    })}
                  </div>
                )}
              </Card>
            );
          })}
        </div>
      )}
      <Dialog open={editing !== null} onClose={() => setEditing(null)} title={editing?.id ? "Edit schedule" : "New schedule"} wide>
        {editing !== null && <RuleForm accounts={accs} initial={editing.id ? editing : undefined} onDone={() => setEditing(null)} />}
      </Dialog>
    </div>
  );
}
