import { useMemo, useState } from "react";
import { ChevronLeft, ChevronRight } from "lucide-react";
import { useApi } from "@/lib/hooks";
import { cn, PLATFORM_COLOR, PLATFORM_LABEL } from "@/lib/utils";
import { Badge, Button, Card, PageHeader, Select, Spinner, StatusBadge } from "@/components/ui";

function startOfMonthGrid(d: Date) {
  const first = new Date(d.getFullYear(), d.getMonth(), 1);
  const offset = (first.getDay() + 6) % 7; // Monday first
  return new Date(first.getFullYear(), first.getMonth(), 1 - offset);
}

export function CalendarPage() {
  const [cursor, setCursor] = useState(() => new Date());
  const [appId, setAppId] = useState("");
  const [selected, setSelected] = useState<string | null>(null);
  const gridStart = useMemo(() => startOfMonthGrid(cursor), [cursor]);
  const gridEnd = new Date(gridStart.getTime() + 42 * 86400000);
  const { data: apps } = useApi<any[]>(["applications"], "/api/applications");
  const url = `/api/calendar?start=${gridStart.toISOString()}&end=${gridEnd.toISOString()}${appId ? `&application_id=${appId}` : ""}`;
  const { data, isLoading } = useApi<any[]>(["calendar", url], url, { refetchInterval: 30000 });
  const byDay = useMemo(() => {
    const m: Record<string, any[]> = {};
    (data ?? []).forEach((i) => {
      const k = new Date(i.scheduled_for).toDateString();
      (m[k] ??= []).push(i);
    });
    return m;
  }, [data]);
  const days = Array.from({ length: 42 }, (_, i) => new Date(gridStart.getTime() + i * 86400000));
  const today = new Date().toDateString();
  const selItems = selected ? byDay[selected] ?? [] : [];
  return (
    <div>
      <PageHeader title="Content Calendar" description="Materialized posts plus projected slots from your schedules (dashed)."
        actions={<>
          <Select className="w-48" value={appId} onChange={(e) => setAppId(e.target.value)}>
            <option value="">All applications</option>{apps?.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
          </Select>
          <Button variant="outline" size="icon" onClick={() => setCursor(new Date(cursor.getFullYear(), cursor.getMonth() - 1, 1))}><ChevronLeft /></Button>
          <Button variant="outline" onClick={() => setCursor(new Date())}>Today</Button>
          <Button variant="outline" size="icon" onClick={() => setCursor(new Date(cursor.getFullYear(), cursor.getMonth() + 1, 1))}><ChevronRight /></Button>
        </>} />
      <div className="mb-3 flex items-center justify-between">
        <h2 className="text-lg font-semibold">{cursor.toLocaleString(undefined, { month: "long", year: "numeric" })}</h2>
        <div className="flex gap-3 text-xs text-muted-foreground">
          {Object.entries(PLATFORM_LABEL).map(([k, v]) => <span key={k} className="flex items-center gap-1.5"><span className={cn("size-2 rounded-full", PLATFORM_COLOR[k])} />{v}</span>)}
        </div>
      </div>
      {isLoading ? <Spinner /> : (
        <div className="grid gap-6 xl:grid-cols-4">
          <Card className="overflow-hidden xl:col-span-3">
            <div className="grid grid-cols-7 border-b bg-muted/40 text-center text-xs font-medium text-muted-foreground">
              {["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].map((d) => <div key={d} className="py-2">{d}</div>)}
            </div>
            <div className="grid grid-cols-7">
              {days.map((d) => {
                const k = d.toDateString();
                const items = byDay[k] ?? [];
                const inMonth = d.getMonth() === cursor.getMonth();
                return (
                  <button key={k} onClick={() => setSelected(k)}
                    className={cn("min-h-28 border-b border-r p-1.5 text-left align-top transition-colors hover:bg-muted/50",
                      !inMonth && "bg-muted/20 text-muted-foreground", selected === k && "ring-2 ring-inset ring-primary")}>
                    <span className={cn("inline-flex size-6 items-center justify-center rounded-full text-xs", k === today && "bg-primary text-primary-foreground")}>{d.getDate()}</span>
                    <div className="mt-1 space-y-0.5">
                      {items.slice(0, 4).map((i, idx) => (
                        <div key={idx} className={cn("flex items-center gap-1 truncate rounded px-1 py-0.5 text-[10px]", i.projected ? "border border-dashed" : "bg-muted")}>
                          <span className={cn("size-1.5 shrink-0 rounded-full", PLATFORM_COLOR[i.platform])} />
                          <span className="truncate">{new Date(i.scheduled_for).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })} {i.application_name}</span>
                        </div>
                      ))}
                      {items.length > 4 && <p className="px-1 text-[10px] text-muted-foreground">+{items.length - 4} more</p>}
                    </div>
                  </button>
                );
              })}
            </div>
          </Card>
          <Card className="p-4">
            <p className="text-sm font-semibold">{selected ? new Date(selected).toLocaleDateString(undefined, { weekday: "long", month: "long", day: "numeric" }) : "Select a day"}</p>
            <div className="mt-3 space-y-2">
              {selected && selItems.length === 0 && <p className="text-sm text-muted-foreground">Nothing scheduled.</p>}
              {selItems.map((i, idx) => (
                <div key={idx} className="rounded-lg border p-2.5">
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-sm font-medium">{new Date(i.scheduled_for).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</span>
                    {i.projected ? <Badge>projected</Badge> : <StatusBadge status={i.status} />}
                  </div>
                  <p className="mt-1 text-xs">{i.application_name}</p>
                  <p className="text-xs text-muted-foreground">{PLATFORM_LABEL[i.platform]} · {i.account_name}</p>
                  {i.thumbnail_url && <img src={i.thumbnail_url} alt="" className="mt-2 h-28 rounded-md object-cover" />}
                </div>
              ))}
            </div>
          </Card>
        </div>
      )}
    </div>
  );
}
