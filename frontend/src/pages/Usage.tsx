import { useApi } from "@/lib/hooks";
import { fmtDate, fmtNum, fmtUsd, label } from "@/lib/utils";
import { Badge, Card, CardContent, CardDescription, CardHeader, CardTitle, Empty, PageHeader, Progress, Spinner, Table, Td, Th } from "@/components/ui";
import { BarsChart } from "@/components/charts";
import { Link } from "react-router-dom";

function Meter({ label, used, limit, fmt }: { label: string; used: number; limit?: number | null; fmt: (n: number) => string }) {
  const ratio = limit ? used / limit : 0;
  return (
    <div>
      <div className="flex justify-between text-xs"><span className="text-muted-foreground">{label}</span><span className="tabular-nums">{fmt(used)} / {limit ? fmt(limit) : "∞"}</span></div>
      <Progress value={ratio} className={`mt-1.5 ${ratio >= 1 ? "[&>div]:bg-danger" : ratio > 0.8 ? "[&>div]:bg-warning" : ""}`} />
    </div>
  );
}

export function Usage() {
  const { data, isLoading } = useApi(["usage"], "/api/usage?days=30", { refetchInterval: 15000 });
  if (isLoading || !data) return <Spinner />;
  const b = data.budget;
  const cacheRead = data.by_day.reduce((s: number, d: any) => s + d.cache_read, 0);
  const input = data.by_day.reduce((s: number, d: any) => s + d.input, 0);
  return (
    <div>
      <PageHeader title="AI Usage & Costs" description="Every Claude request: tokens, cache hits, estimated cost, purpose and job." />
      {b.exceeded && <div className="mb-4 rounded-lg border border-danger/40 bg-danger/10 px-4 py-3 text-sm text-danger">Budget reached: {b.reasons.join("; ")}. AI jobs are paused until the window resets.</div>}
      <div className="grid gap-6 lg:grid-cols-3">
        <Card>
          <CardHeader><div><CardTitle>Budgets</CardTitle><CardDescription>Global limits · <Link className="text-primary" to="/settings">edit</Link>{b.cost_saving_mode && <Badge tone="primary" className="ml-2">cost-saving mode</Badge>}</CardDescription></div></CardHeader>
          <CardContent className="space-y-4">
            <Meter label="Claude calls today" used={b.calls_today} limit={b.limits.daily_calls} fmt={fmtNum} />
            <Meter label="Tokens this month" used={b.tokens_month} limit={b.limits.monthly_tokens} fmt={fmtNum} />
            <Meter label="Spend this month" used={b.cost_month} limit={b.limits.monthly_cost_usd} fmt={fmtUsd} />
            <p className="text-xs text-muted-foreground">Max tokens per job: {fmtNum(b.limits.max_tokens_per_job)} · retries per plan: {b.limits.max_retries}</p>
            <p className="text-xs text-muted-foreground">Prompt cache: {fmtNum(cacheRead)} tokens read from cache vs {fmtNum(input)} uncached input (30 days).</p>
          </CardContent>
        </Card>
        <Card className="lg:col-span-2">
          <CardHeader><div><CardTitle>Estimated cost per day</CardTitle><CardDescription>USD, last 30 days</CardDescription></div></CardHeader>
          <CardContent>
            {data.by_day.length ? <BarsChart data={data.by_day.map((d: any) => ({ day: d.day.slice(5), cost: d.cost }))} x="day"
              series={[{ key: "cost", name: "Cost" }]} format={(v) => `$${v.toFixed(2)}`} /> : <Empty title="No Claude calls yet" />}
          </CardContent>
        </Card>
      </div>
      <div className="mt-6 grid gap-6 lg:grid-cols-2">
        {[["By application", data.by_application, "application"], ["By purpose", data.by_purpose, "purpose"]].map(([title, rows, key]: any) => (
          <Card key={title}>
            <CardHeader><CardTitle>{title}</CardTitle></CardHeader>
            <CardContent>
              <Table>
                <thead><tr><Th>{label(key)}</Th><Th className="text-right">Calls</Th><Th className="text-right">Tokens</Th><Th className="text-right">Cost</Th></tr></thead>
                <tbody>{rows.map((r: any) => (
                  <tr key={r[key]}><Td>{label(r[key])}</Td><Td className="text-right tabular-nums">{r.calls}</Td><Td className="text-right tabular-nums">{fmtNum(r.tokens)}</Td><Td className="text-right tabular-nums">{fmtUsd(r.cost)}</Td></tr>
                ))}</tbody>
              </Table>
            </CardContent>
          </Card>
        ))}
      </div>
      <Card className="mt-6">
        <CardHeader><CardTitle>Requests</CardTitle></CardHeader>
        <CardContent>
          {data.records.length === 0 ? <Empty title="No requests" /> : (
            <Table>
              <thead><tr><Th>Time</Th><Th>Application</Th><Th>Purpose</Th><Th>Model</Th><Th className="text-right">In</Th><Th className="text-right">Out</Th><Th className="text-right">Cache read</Th><Th className="text-right">Cost</Th><Th>Result</Th></tr></thead>
              <tbody>{data.records.map((r: any) => (
                <tr key={r.id}>
                  <Td className="whitespace-nowrap text-xs">{fmtDate(r.created_at)}</Td><Td>{r.application ?? "—"}</Td><Td>{label(r.purpose)}</Td>
                  <Td className="font-mono text-xs">{r.model}</Td><Td className="text-right tabular-nums">{fmtNum(r.input_tokens)}</Td>
                  <Td className="text-right tabular-nums">{fmtNum(r.output_tokens)}</Td><Td className="text-right tabular-nums">{fmtNum(r.cache_read_tokens)}</Td>
                  <Td className="text-right tabular-nums">{fmtUsd(r.estimated_cost)}</Td>
                  <Td>{r.success ? <Badge tone="success">ok</Badge> : <Badge tone="danger" title={r.error}>error</Badge>}</Td>
                </tr>
              ))}</tbody>
            </Table>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
