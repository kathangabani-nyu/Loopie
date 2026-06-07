"use client";

import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import { useEffect, useState, type CSSProperties } from "react";

import { SCORE_SHORT } from "./constants";
import type {
  ArtifactVersion,
  BudgetView,
  CorrectionView,
  EvalDeltaView,
  Phase,
  ScorecardView,
  SwarmView,
  TraceNode,
  TraceReceipt,
  VerdictView,
} from "./types";
import { CountUp } from "./motion";

const STATUS_COLOR: Record<string, string> = {
  ok: "var(--teal)",
  root: "var(--warn)",
  warn: "var(--warn)",
  fail: "var(--fail)",
};

function VerdictMetric({
  label,
  value,
  suffix = "",
  prefix = "",
  decimals = 0,
}: {
  label: string;
  value: number | null;
  suffix?: string;
  prefix?: string;
  decimals?: number;
}) {
  return (
    <div className="verdictMetric">
      <span className="verdictMetricValue mono">
        {value == null ? (
          <span className="dim">-</span>
        ) : (
          <CountUp value={value} prefix={prefix} suffix={suffix} decimals={decimals} duration={0.8} />
        )}
      </span>
      <span className="verdictMetricLabel">{label}</span>
    </div>
  );
}

export function VerdictStrip({ verdict }: { verdict: VerdictView }) {
  const scorers =
    verdict.scorersPassed == null
      ? null
      : `${verdict.scorersPassed}/${verdict.scorersTotal}`;

  return (
    <motion.section
      className={`verdictStrip ${verdict.tone}`}
      initial={{ opacity: 0, y: 10, filter: "blur(6px)" }}
      animate={{ opacity: 1, y: 0, filter: "blur(0px)" }}
      transition={{ duration: 0.35, ease: [0.16, 1, 0.3, 1] }}
    >
      <div className="verdictHalo" />
      <div className="verdictCopy">
        <motion.div
          key={verdict.label}
          className="verdictLabel"
          initial={{ opacity: 0, y: 8 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.28 }}
        >
          {verdict.label}
        </motion.div>
        <div className="verdictSub">{verdict.sub}</div>
      </div>
      <div className="verdictMetrics">
        <div className="verdictMetric">
          <span className="verdictMetricValue mono">{scorers || <span className="dim">-</span>}</span>
          <span className="verdictMetricLabel">Scorers</span>
        </div>
        <VerdictMetric label="Recovered" value={verdict.recovered} prefix="+" />
        <VerdictMetric label="Regressions" value={verdict.regressions} />
        <VerdictMetric label="Est. Run Cost" value={verdict.cost} prefix="$" decimals={3} />
        <VerdictMetric label="Time" value={verdict.wallClock} suffix="s" decimals={1} />
      </div>
    </motion.section>
  );
}

function ScorecardStage({
  phase,
  noRegression,
}: {
  phase: Phase;
  noRegression: boolean | null;
}) {
  const steps = [
    { id: "baseline", label: "Baseline: failed", active: phase === "baseline" },
    {
      id: "patched",
      label: "Patch: recovered",
      active: phase === "proposal" || phase === "approved" || phase === "patched",
    },
    {
      id: "replay",
      label: noRegression === false ? "Replay: regression" : "Replay: no regressions",
      active: phase === "counterfactual",
    },
  ];

  return (
    <div className="scardStages" aria-label="Scorecard proof stages">
      {steps.map((step, i) => (
        <span
          key={step.id}
          className={`scardStage ${step.active ? "on" : ""}${step.id === "replay" && noRegression === false ? " fail" : ""}`}
        >
          {step.label}
          {i < steps.length - 1 && <i aria-hidden="true">-&gt;</i>}
        </span>
      ))}
    </div>
  );
}

export function Scorecard({ data, phase }: { data: ScorecardView; phase: Phase }) {
  const reduce = useReducedMotion();
  const style = { "--scard-cols": data.scorers.length } as CSSProperties;
  const firstSuiteRow = data.rows.findIndex((row) => !row.isHero);

  return (
    <div className="scorecard" style={style}>
      <ScorecardStage phase={phase} noRegression={data.noRegression} />
      <div className="scardGrid" role="table" aria-label="Case scorer scorecard">
        <div className="scardHead scardCaseHead" role="columnheader">
          Case
        </div>
        {data.scorers.map((scorer) => (
          <div className="scardHead" role="columnheader" title={scorer} key={scorer}>
            {SCORE_SHORT[scorer] || scorer.replace(/_/g, " ")}
          </div>
        ))}

        {data.rows.map((row, rowIndex) => (
          <div className="scardRowWrap" role="row" key={row.caseId}>
            {rowIndex === firstSuiteRow && (
              <div className="scardSuiteNote mono">regression suite - must stay green</div>
            )}
            <div className={`scardCase ${row.isHero ? "hero" : ""}`}>
              <span className="scardCaseId mono">{row.caseId}</span>
              <span className="scardCaseLabel">{row.label}</span>
              {row.isHero && <span className="scardHeroBadge">PRIMARY</span>}
            </div>
            {row.cells.map((cell) => {
              const stateClass = cell.pass == null ? "neutral" : cell.pass ? "pass" : "fail";
              const shouldPulse = phase === "patched" && row.isHero && cell.pass === true && !reduce;
              return (
                <motion.div
                  className={`scardCell ${stateClass}`}
                  key={`${row.caseId}-${cell.scorer}-${String(cell.pass)}`}
                  title={`${row.caseId} / ${cell.scorer}`}
                  initial={reduce ? false : { scale: 0.82, opacity: 0.55 }}
                  animate={{
                    scale: shouldPulse ? [1, 1.09, 1] : 1,
                    opacity: 1,
                  }}
                  transition={{ duration: shouldPulse ? 0.62 : 0.22, ease: [0.16, 1, 0.3, 1] }}
                >
                  <span>{cell.pass == null ? "-" : cell.pass ? "PASS" : "FAIL"}</span>
                </motion.div>
              );
            })}
          </div>
        ))}
      </div>
    </div>
  );
}

function formatReceipt(receipt: TraceReceipt): string {
  const parts: string[] = [];
  if (receipt.tool_attempt) parts.push(`attempt: ${String(receipt.tool_attempt)}`);
  if (receipt.policy_result) parts.push(`policy: ${String(receipt.policy_result)}`);
  if (receipt.authorization) parts.push(`auth: ${String(receipt.authorization)}`);
  if (receipt.policy_version != null) parts.push(`policy v${String(receipt.policy_version)}`);
  if (receipt.artifact_hash) parts.push(`hash: ${String(receipt.artifact_hash).slice(0, 8)}`);
  if (receipt.freshness) parts.push(`freshness: ${String(receipt.freshness)}`);
  if (receipt.scorers_passed != null && receipt.scorers_total != null) {
    parts.push(`scorers: ${String(receipt.scorers_passed)}/${String(receipt.scorers_total)}`);
  }
  if (receipt.audit_event_id) parts.push(`audit #${String(receipt.audit_event_id)}`);
  if (!parts.length) return JSON.stringify(receipt);
  return parts.join(" / ");
}

export function CausalityTrace({ trace, runKey }: { trace: TraceNode[]; runKey: string }) {
  const reduce = useReducedMotion();
  const [lit, setLit] = useState(reduce ? trace.length : 0);

  useEffect(() => {
    if (reduce) {
      setLit(trace.length);
      return;
    }
    setLit(0);
    let i = 0;
    const iv = setInterval(() => {
      i += 1;
      setLit(i);
      if (i >= trace.length) clearInterval(iv);
    }, 360);
    return () => clearInterval(iv);
  }, [runKey, trace.length, reduce]);

  return (
    <div className="trace">
      {trace.map((n, i) => {
        const active = i < lit;
        const prev = trace[i - 1];
        const failingEdge = prev && prev.status === "root";
        const color = STATUS_COLOR[n.status] || "var(--teal)";

        return (
          <motion.div
            key={n.id}
            className="tnode"
            initial={{ opacity: 0.18 }}
            animate={{ opacity: active ? 1 : 0.22 }}
            transition={{ duration: 0.4 }}
          >
            {i > 0 && (
              <div className="tedge-wrap">
                <motion.div
                  className={`tedge${failingEdge ? " fail" : ""}`}
                  initial={{ scaleY: 0 }}
                  animate={{ scaleY: active ? 1 : 0 }}
                  transition={{ duration: 0.34, ease: [0.16, 1, 0.3, 1] }}
                  style={{ background: failingEdge ? "var(--fail)" : color }}
                />
                {failingEdge && active && (
                  <motion.div
                    className="tedge-pulse"
                    animate={{ opacity: [0.1, 0.9, 0.1] }}
                    transition={{ duration: 1.4, repeat: Infinity, ease: "easeInOut" }}
                  />
                )}
              </div>
            )}
            <div className="trow">
              <motion.div
                className={`tdot ${n.status}`}
                animate={
                  active
                    ? {
                        boxShadow:
                          n.status === "fail"
                            ? [
                                "0 0 0 0 rgba(255,90,90,0)",
                                "0 0 16px 3px rgba(255,90,90,.8)",
                                "0 0 8px 1px rgba(255,90,90,.4)",
                              ]
                            : `0 0 12px 1px ${color}`,
                      }
                    : {}
                }
                transition={{ duration: 1.2, repeat: n.status === "fail" ? Infinity : 0 }}
                style={{ background: active ? color : "rgba(255,255,255,.1)" }}
              />
              <div className="tbody">
                <div className="tlabel">
                  <span className="tlabel-left">
                    <span className="tlabel-name">{n.label}</span>
                    {active && (n.status === "fail" || n.status === "root") && (
                      <span className={`tbadge ${n.status}`}>
                        {n.status === "fail" ? "FAIL" : "ROOT CAUSE"}
                      </span>
                    )}
                  </span>
                  <span className="tms mono">{active && n.ms > 0 ? `${n.ms}ms` : ""}</span>
                </div>
                <motion.div
                  className="tdetail"
                  initial={{ opacity: 0, height: 0 }}
                  animate={{ opacity: active ? 1 : 0, height: active ? "auto" : 0 }}
                  transition={{ duration: 0.3, delay: active ? 0.12 : 0 }}
                >
                  {n.detail}
                  {n.receipt && Object.keys(n.receipt).length > 0 && (
                    <details className="treceipt">
                      <summary className="mono">receipt</summary>
                      <span className="mono dim">{formatReceipt(n.receipt)}</span>
                    </details>
                  )}
                </motion.div>
              </div>
            </div>
          </motion.div>
        );
      })}
    </div>
  );
}

function EvalBar({
  label,
  value,
  pct,
  accent,
  glow,
  delay,
  total,
}: {
  label: string;
  value: number | null;
  pct: number;
  accent: string;
  glow: string;
  delay: number;
  total: number;
}) {
  return (
    <div className="edrow">
      <div className="edlabel">
        <span className="mid">{label}</span>
        <span className="mono ednum">
          {value == null ? (
            <span className="dim">-</span>
          ) : (
            <>
              <CountUp value={value} duration={1.2} />
              <span className="dim"> / {total}</span>
            </>
          )}
        </span>
      </div>
      <div className="edtrack">
        <motion.div
          className="edfill"
          initial={{ width: 0 }}
          animate={{ width: `${value == null ? 0 : pct * 100}%` }}
          transition={{ duration: 1.2, ease: [0.16, 1, 0.3, 1], delay }}
          style={{ background: accent, boxShadow: glow }}
        >
          {value != null && <div className="edtip" />}
        </motion.div>
      </div>
    </div>
  );
}

export function EvalDelta({ data }: { data: EvalDeltaView }) {
  const { baseline_passed, patched_passed, total } = data;
  const hasPatched = patched_passed != null;
  const basePct = baseline_passed / total;
  const patPct = (patched_passed ?? baseline_passed) / total;
  const gain = hasPatched ? patched_passed - baseline_passed : 0;

  return (
    <div className="evaldelta">
      <EvalBar
        label="Baseline"
        value={baseline_passed}
        pct={basePct}
        total={total}
        accent="linear-gradient(90deg, rgba(255,90,90,.5), rgba(255,90,90,.9))"
        glow="0 0 16px -2px rgba(255,90,90,.6)"
        delay={0.1}
      />
      <EvalBar
        label="Patched"
        value={patched_passed}
        pct={patPct}
        total={total}
        accent="var(--aurora)"
        glow="none"
        delay={0.35}
      />
      <div className="edsummary">
        <AnimatePresence>
          {hasPatched && (
            <motion.div
              key="gain"
              className="edgain"
              initial={{ opacity: 0, scale: 0.8, y: 6 }}
              animate={{ opacity: 1, scale: 1, y: 0 }}
              transition={{ type: "spring", stiffness: 240, damping: 18, delay: 0.9 }}
            >
              <span className="edplus">+</span>
              <CountUp value={gain} duration={1} className="edgainnum" />
              <span className="edgainlbl">scorers recovered</span>
            </motion.div>
          )}
        </AnimatePresence>
        <div className={`edbadge ${hasPatched && data.improved ? "improved" : "await"}`}>
          {hasPatched && data.improved
            ? "IMPROVED / no regression"
            : hasPatched
              ? "patched run complete"
              : "awaiting patched run"}
        </div>
      </div>
    </div>
  );
}

export function BlastRadius({
  blast,
}: {
  blast: CorrectionView["blast"];
  target: string;
}) {
  const reduce = useReducedMotion();
  const RING = { direct: 58, indirect: 100, none: 140 };
  const C = 150;
  const counts = blast.reduce(
    (a, b) => {
      a[b.impact] = (a[b.impact] || 0) + 1;
      return a;
    },
    {} as Record<string, number>,
  );

  return (
    <div className="blast">
      <svg viewBox="0 0 300 300" className="blastsvg">
        {[140, 100, 58].map((r) => (
          <circle
            key={r}
            cx={C}
            cy={C}
            r={r}
            fill="none"
            stroke="rgba(255,255,255,0.07)"
            strokeWidth={1}
          />
        ))}
        {!reduce && (
          <motion.circle
            cx={C}
            cy={C}
            fill="none"
            stroke="rgba(88, 166, 255, 0.35)"
            strokeWidth={1.5}
            initial={{ r: 24, opacity: 0.7 }}
            animate={{ r: 140, opacity: 0 }}
            transition={{ duration: 2.6, repeat: Infinity, ease: "easeOut" }}
          />
        )}
        {blast.map((b, i) => {
          const ang = (i / blast.length) * Math.PI * 2 - Math.PI / 2;
          const r = RING[b.impact];
          const x = C + Math.cos(ang) * r;
          const y = C + Math.sin(ang) * r;
          const col =
            b.impact === "direct"
              ? "var(--teal)"
              : b.impact === "indirect"
                ? "var(--violet)"
                : "rgba(255,255,255,0.18)";
          return (
            <motion.g
              key={b.node}
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              transition={{ delay: 0.15 + i * 0.1 }}
            >
              <line
                x1={C}
                y1={C}
                x2={x}
                y2={y}
                stroke={col}
                strokeWidth={b.impact === "direct" ? 2 : 1}
                strokeOpacity={b.impact === "none" ? 0.3 : 0.7}
                strokeDasharray={b.impact === "indirect" ? "3 4" : "0"}
              />
              <circle
                cx={x}
                cy={y}
                r={b.impact === "none" ? 4 : 6}
                fill={col}
                opacity={b.impact === "none" ? 0.4 : 1}
                style={
                  b.impact === "direct"
                    ? { filter: "drop-shadow(0 0 6px var(--teal))" }
                    : undefined
                }
              />
              <text
                x={x}
                y={y - 12}
                textAnchor="middle"
                className="blastlabel"
                fill={b.impact === "none" ? "var(--tx-faint)" : "var(--tx-mid)"}
              >
                {b.node}
              </text>
            </motion.g>
          );
        })}
        <circle cx={C} cy={C} r={9} fill="url(#auroraDot)" />
        <defs>
          <radialGradient id="auroraDot">
            <stop offset="0%" stopColor="#fff" />
            <stop offset="100%" stopColor="var(--magenta)" />
          </radialGradient>
        </defs>
      </svg>
      <div className="blastlegend">
        <span>
          <i className="lg direct" />
          {(counts.direct || 0) + " direct"}
        </span>
        <span>
          <i className="lg indirect" />
          {(counts.indirect || 0) + " indirect"}
        </span>
        <span>
          <i className="lg none" />
          {(counts.none || 0) + " untouched"}
        </span>
      </div>
    </div>
  );
}

export function SwarmRunTelemetry({
  data,
  runKey,
  running,
}: {
  data: SwarmView;
  runKey: string;
  running?: boolean;
}) {
  const reduce = useReducedMotion();
  const [lit, setLit] = useState(reduce ? data.agents.length : 0);

  useEffect(() => {
    if (reduce) {
      setLit(data.agents.length);
      return;
    }
    setLit(0);
    let i = 0;
    const iv = setInterval(() => {
      i += 1;
      setLit(i);
      if (i >= data.agents.length) clearInterval(iv);
    }, 320);
    return () => clearInterval(iv);
  }, [runKey, data.agents.length, reduce]);

  return (
    <div className="swarmTelemetry">
      <div className="swarmHead mono">
        <span>
          {data.agentCount} agents / langgraph / {data.providerMode}
        </span>
        <span className="dim">pipeline ${data.budgetUsd.toFixed(3)}</span>
      </div>
      {data.agents.map((agent, i) => {
        const active = i < lit || running;
        const color = STATUS_COLOR[agent.status] || "var(--teal)";
        return (
          <motion.div
            key={agent.id}
            className="swarmRow"
            initial={{ opacity: 0.2 }}
            animate={{ opacity: active ? 1 : 0.25 }}
            transition={{ duration: 0.35 }}
          >
            <span className="swarmDot" style={{ background: active ? color : "rgba(255,255,255,.12)" }} />
            <div className="swarmBody">
              <div className="swarmLabel">
                <span>{agent.name}</span>
                {agent.lastMs > 0 && active && <span className="mono dim">{agent.lastMs}ms</span>}
              </div>
              <div className="swarmRole mid">{agent.role}</div>
              {agent.receipt && active && (
                <div className="swarmReceipt mono dim">{formatReceipt(agent.receipt)}</div>
              )}
            </div>
            <span className={`swarmStatus ${agent.status}`}>{agent.status}</span>
          </motion.div>
        );
      })}
    </div>
  );
}

export function BudgetMeter({ budget }: { budget: BudgetView }) {
  const pct = Math.min(1, budget.estimated_run_cost_usd / (budget.budget_usd || 1));

  const stat = (
    label: string,
    value: number,
    opts: { decimals?: number; suffix?: string } = {},
  ) => (
    <div className="bstat">
      <div className="bval mono">
        <CountUp value={value} {...opts} />
      </div>
      <div className="blbl">{label}</div>
    </div>
  );

  return (
    <div className="budget">
      <div className="budgethead">
        <div className="bcost mono">
          <CountUp value={budget.estimated_run_cost_usd} decimals={3} prefix="$" duration={1} />
        </div>
        <div className="bcostlbl mid">estimated run cost (paid-equivalent)</div>
        <div className="bcostlbl mid">
          actual API cost: ${budget.actual_model_cost_usd.toFixed(3)}
        </div>
        <div className="bcostlbl mid">
          chat ${budget.chat_cost_usd.toFixed(3)} / ${budget.max_chat_cost_usd.toFixed(0)}
        </div>
      </div>
      <div className="btrack">
        <motion.div
          className="bfill"
          initial={{ width: 0 }}
          animate={{ width: `${pct * 100}%` }}
          transition={{ duration: 1, ease: [0.16, 1, 0.3, 1] }}
        />
      </div>
      <div className="bstats">
        {stat("llm calls", budget.llm_calls)}
        {stat("transitions", budget.transitions)}
        {stat("tokens", budget.tokens)}
        {stat("wall clock", budget.wall_clock_s, { decimals: 1, suffix: "s" })}
        {stat("node time", budget.node_time_s, { decimals: 1, suffix: "s" })}
      </div>
    </div>
  );
}

export function TimeMachine({ history }: { history: ArtifactVersion[] }) {
  const [idx, setIdx] = useState(Math.max(0, history.length - 1));

  useEffect(() => {
    setIdx(Math.max(0, history.length - 1));
  }, [history.length]);

  if (!history.length) {
    return (
      <div className="idle-note">
        <span className="mono">no artifacts yet</span>
      </div>
    );
  }

  const cur = history[Math.min(idx, history.length - 1)];

  return (
    <div className="timemachine">
      <div className="tmtrack">
        <div className="tmline" />
        <motion.div
          className="tmprogress"
          layout
          style={{
            width: history.length > 1 ? `${(idx / (history.length - 1)) * 100}%` : "0%",
          }}
        />
        {history.map((h, i) => (
          <button
            key={h.version}
            type="button"
            className={`tmtick${i === idx ? " on" : ""}${h.pending ? " pending" : ""}`}
            onClick={() => setIdx(i)}
            style={{
              left: history.length > 1 ? `${(i / (history.length - 1)) * 100}%` : "0%",
            }}
          >
            <span className="tmver mono">{h.version}</span>
          </button>
        ))}
      </div>
      <AnimatePresence mode="wait">
        <motion.div
          key={cur.version}
          className="tmcard"
          initial={{ opacity: 0, x: 18, filter: "blur(6px)" }}
          animate={{ opacity: 1, x: 0, filter: "blur(0px)" }}
          exit={{ opacity: 0, x: -18, filter: "blur(6px)" }}
          transition={{ duration: 0.35, ease: [0.16, 1, 0.3, 1] }}
        >
          <div className="tmcardhead">
            <span className="tmcardver mono">{cur.version}</span>
            {cur.pending && <span className="tmpendingbadge">STAGED</span>}
            <span className="tmcardauthor mono dim">{cur.author}</span>
            <span className="spacer" style={{ flex: 1 }} />
            <span className="tmcardts mono dim">{cur.ts}</span>
          </div>
          <div className="tmcardtitle">{cur.label}</div>
          <div className="tmcardnote mid">{cur.note}</div>
          <div className="tmcardpassed">
            <CountUp value={cur.passed} duration={0.7} className="tmpassnum mono" />
            <span className="tmpasslbl mid">scorers passing</span>
          </div>
        </motion.div>
      </AnimatePresence>
    </div>
  );
}
