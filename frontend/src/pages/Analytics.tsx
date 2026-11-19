import { useState } from "react";
import { ExternalLink } from "lucide-react";
import { useApi } from "@/lib/hooks";
import { fmtNum, fmtUsd, PLATFORM_LABEL } from "@/lib/utils";
import { Card, CardContent, CardDescription, CardHeader, CardTitle, Empty, PageHeader, Select, Spinner, Table, Td, Th } from "@/components/ui";
import { BarsChart } from "@/components/charts";

function Tile({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <Card className="p-4">
      <p className="text-xs text-muted-foreground">{label}</p>
      <p className="mt-1 text-2xl font-semibold tracking-tight">{value}</p>
      {sub && <p className="text-xs text-muted-foreground">{sub}</p>}
    </Card>
  );
}

const pct = (v: number | null | undefined) => (v === null || v === undefined ? "n/a" : `${Math.round(v * 100)}%`);

export function Analytics() {
  const [days, setDays] = useState(30);
  const { data, isLoading } = useApi(["analytics", days], `/api/analytics?days=${days}`);
  if (isLoading || !data) return <Spinner />;
  return (
    <div>
      <PageHeader title="Analytics" description="Production, publishing and engagement from local records and official platform APIs."
        actions={<Select className="w-36" value={days} onChange={(e) => setDays(Number(e.target.value))}>
          <option value={7}>Last 7 days</option><option value={30}>Last 30 days</option><option value={90}>Last 90 days</option>
        </Select>} />
      <div className="grid grid-cols-2 gap-4 md:grid-cols-3 xl:grid-cols-6">
        <Tile label="Publishing success" value={pct(data.success_rate)} sub="published / attempted" />
        <Tile label="On-schedule" value={pct(data.schedule_adherence.rate)} sub={`${data.schedule_adherence.on_time} on time · ${data.schedule_adherence.late} late`} />
        <Tile label="Failed jobs" value={fmtNum(data.failed_jobs)} sub={`of ${fmtNum(data.total_jobs)} jobs`} />
        <Tile label="Avg render time" value={data.avg_render_seconds ? `${data.avg_render_seconds}s` : "n/a"} sub={`${data.videos_per_day} videos/day`} />
        <Tile label="Content backlog" value={fmtNum(data.backlog)} sub="ready, not yet scheduled" />
        <Tile label="Claude spend" value={fmtUsd(data.cost)} sub={`${fmtNum(data.tokens)} tokens`} />
      </div>
      <div className="mt-6 grid gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader><div><CardTitle>Videos generated vs published</CardTitle><CardDescription>Per day</CardDescription></div></CardHeader>
          <CardContent>
            {data.by_day.length ? <BarsChart data={data.by_day.map((d: any) => ({ ...d, day: d.day.slice(5) }))} x="day"
              series={[{ key: "generated", name: "Generated" }, { key: "published", name: "Published" }]} /> : <Empty title="No activity yet" />}
          </CardContent>
        </Card>
        <Card>
          <CardHeader><div><CardTitle>Outcomes by platform</CardTitle><CardDescription>Slots in this period</CardDescription></div></CardHeader>
          <CardContent>
            {data.by_platform.length ? <BarsChart data={data.by_platform.map((p: any) => ({ ...p, platform: PLATFORM_LABEL[p.platform] ?? p.platform }))} x="platform"
              series={[{ key: "published", name: "Published" }, { key: "exported", name: "Exported" }, { key: "failed", name: "Failed" }]} /> : <Empty title="No publications yet" />}
          </CardContent>
        </Card>
        <Card>
          <CardHeader><CardTitle>Videos by application</CardTitle></CardHeader>
          <CardContent>
            {data.generated_by_application.length ? <BarsChart data={data.generated_by_application} x="application" series={[{ key: "videos", name: "Videos" }]} />
              : <Empty title="No videos yet" />}
          </CardContent>
        </Card>
        <Card>
          <CardHeader><div><CardTitle>Engagement</CardTitle><CardDescription>{data.engagement_note}</CardDescription></div></CardHeader>
          <CardContent>
            {data.engagement.length === 0 ? <Empty title="No metrics yet">Metrics are fetched every 6 hours for published posts on connected accounts.</Empty> : (
              <Table>
                <thead><tr><Th>Post</Th><Th className="text-right">Views</Th><Th className="text-right">Likes</Th><Th className="text-right">Comments</Th><Th className="text-right">Shares</Th></tr></thead>
                <tbody>
                  {data.engagement.map((e: any) => (
                    <tr key={e.publication_id}>
                      <Td><p className="text-sm">{e.application}</p><p className="text-xs text-muted-foreground">{PLATFORM_LABEL[e.platform]}
                        {e.url && <a href={e.url} target="_blank" rel="noreferrer" className="ml-1 inline-flex text-primary"><ExternalLink className="size-3" /></a>}</p></Td>
                      {(["views", "likes", "comments", "shares"] as const).map((k) => (
                        <Td key={k} className="text-right tabular-nums" title={e.unavailable?.[k] ?? e.unavailable?.all}>
                          {e[k] === null ? <span className="text-muted-foreground">unavailable</span> : fmtNum(e[k])}
                        </Td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </Table>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
