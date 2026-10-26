"use client";

import {
  Bar,
  BarChart,
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { formatDate } from "@/lib/utils";

const axis = { fontSize: 12, fill: "var(--muted-foreground)" };
const tooltipStyle = {
  background: "var(--card)",
  border: "1px solid var(--border)",
  borderRadius: 8,
  fontSize: 12,
  color: "var(--foreground)",
};

export function TrendChart({
  data,
  label,
  height = 200,
}: {
  data: { date: string; score: number }[];
  label: string;
  height?: number;
}) {
  const rows = data.map((d) => ({ ...d, day: formatDate(d.date, { year: undefined }) }));
  return (
    <div style={{ height }} role="img" aria-label={`${label} trend: ${data.map((d) => Math.round(d.score)).join(", ")}`}>
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={rows} margin={{ top: 8, right: 8, bottom: 0, left: -20 }}>
          <CartesianGrid stroke="var(--border)" strokeDasharray="3 3" vertical={false} />
          <XAxis dataKey="day" tick={axis} tickLine={false} axisLine={false} />
          <YAxis domain={[0, 100]} tick={axis} tickLine={false} axisLine={false} />
          <Tooltip contentStyle={tooltipStyle} formatter={(v) => [Math.round(Number(v)), label]} />
          <Line type="monotone" dataKey="score" stroke="var(--primary)" strokeWidth={2} dot={{ r: 3 }} />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

export function GapHistoryChart({
  data,
  height = 200,
}: {
  data: { created_at: string; matched: number; partial: number; missing: number }[];
  height?: number;
}) {
  const rows = data.map((d) => ({ ...d, day: formatDate(d.created_at, { year: undefined }) }));
  return (
    <div style={{ height }} role="img" aria-label="Skill gap status per analysis">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={rows} margin={{ top: 8, right: 8, bottom: 0, left: -20 }}>
          <CartesianGrid stroke="var(--border)" strokeDasharray="3 3" vertical={false} />
          <XAxis dataKey="day" tick={axis} tickLine={false} axisLine={false} />
          <YAxis allowDecimals={false} tick={axis} tickLine={false} axisLine={false} />
          <Tooltip contentStyle={tooltipStyle} />
          <Bar dataKey="matched" stackId="a" fill="var(--success)" name="Matched" />
          <Bar dataKey="partial" stackId="a" fill="var(--warning)" name="Partial" />
          <Bar dataKey="missing" stackId="a" fill="var(--danger)" name="Missing" radius={[4, 4, 0, 0]} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

export function CompetencyBars({ data }: { data: Record<string, number> }) {
  const rows = Object.entries(data)
    .map(([skill, value]) => ({ skill, value }))
    .sort((a, b) => a.value - b.value);
  return (
    <div style={{ height: Math.max(120, rows.length * 36) }} role="img" aria-label={`Competency scores: ${rows.map((r) => `${r.skill} ${Math.round(r.value)}`).join(", ")}`}>
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={rows} layout="vertical" margin={{ top: 0, right: 16, bottom: 0, left: 8 }}>
          <XAxis type="number" domain={[0, 100]} tick={axis} tickLine={false} axisLine={false} />
          <YAxis type="category" dataKey="skill" tick={axis} tickLine={false} axisLine={false} width={150} />
          <Tooltip contentStyle={tooltipStyle} formatter={(v) => [Math.round(Number(v)), "Score"]} />
          <Bar dataKey="value" fill="var(--primary)" radius={[0, 4, 4, 0]} barSize={16} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
