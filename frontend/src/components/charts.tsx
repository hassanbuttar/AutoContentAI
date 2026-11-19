// Chart building blocks: thin bars with rounded data-ends, recessive grid, hover tooltips,
// legend for >= 2 series, text in text tokens (never series colors). Max 3 series (validated slots 1-3).
import { Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

export const SERIES = ["var(--series-1)", "var(--series-2)", "var(--series-3)"];

const axis = { stroke: "hsl(var(--muted-foreground))", fontSize: 11, tickLine: false, axisLine: false } as const;

function ChartTooltip({ active, payload, label, format }: any) {
  if (!active || !payload?.length) return null;
  return (
    <div className="rounded-lg border bg-card px-3 py-2 text-xs shadow-lg">
      <p className="mb-1 font-medium">{label}</p>
      {payload.map((p: any) => (
        <p key={p.dataKey} className="flex items-center gap-2 text-muted-foreground">
          <span className="size-2 rounded-sm" style={{ background: p.color }} />
          {p.name}: <span className="font-medium text-foreground">{format ? format(p.value) : p.value}</span>
        </p>
      ))}
    </div>
  );
}

export function BarsChart({ data, x, series, height = 240, format }: {
  data: any[]; x: string; series: { key: string; name: string }[]; height?: number; format?: (v: number) => string;
}) {
  return (
    <ResponsiveContainer width="100%" height={height}>
      <BarChart data={data} margin={{ top: 8, right: 8, left: -12, bottom: 0 }} barGap={2} barCategoryGap="28%">
        <CartesianGrid vertical={false} stroke="hsl(var(--border))" strokeDasharray="0" />
        <XAxis dataKey={x} {...axis} />
        <YAxis {...axis} allowDecimals={false} tickFormatter={format} width={48} />
        <Tooltip content={<ChartTooltip format={format} />} cursor={{ fill: "hsl(var(--muted))", opacity: 0.6 }} />
        {series.length > 1 && <Legend iconType="square" iconSize={8} wrapperStyle={{ fontSize: 12, color: "hsl(var(--muted-foreground))" }} />}
        {series.slice(0, 3).map((s, i) => (
          <Bar key={s.key} dataKey={s.key} name={s.name} fill={SERIES[i]} radius={[4, 4, 0, 0]} maxBarSize={28} />
        ))}
      </BarChart>
    </ResponsiveContainer>
  );
}
