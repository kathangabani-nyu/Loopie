"use client";

import { AnimatePresence, motion } from "framer-motion";
import { useEffect, useRef, type ReactNode } from "react";

import { SCORE_ORDER } from "./constants";
import type { CorrectionView, DemoBriefView, FailureView, StreamEvent, WeaveProofView } from "./types";
import { CountUp, riseIn, sceneStagger, useRipple } from "./motion";
import { BlastRadius } from "./viz";

export function Panel({
  title,
  subtitle,
  tag,
  live,
  area,
  children,
  scroll = true,
}: {
  title: string;
  subtitle?: string;
  tag?: string | null;
  live?: boolean;
  area: string;
  children: ReactNode;
  scroll?: boolean;
}) {
  return (
    <section className={`region r-${area}`}>
      <div
        className={`glass${live ? " live" : ""}`}
        style={{ height: "100%", display: "flex", flexDirection: "column" }}
      >
        <div className="panel-pad" style={{ paddingBottom: 10 }}>
          <div className="phead">
            <span
              className="dot"
              style={
                live
                  ? undefined
                  : { background: "rgba(255,255,255,.2)", boxShadow: "none" }
              }
            />
            <span>
              {title}
              {subtitle ? <span className="panel-sub mid"> - {subtitle}</span> : null}
            </span>
            <span className="spacer" />
            {tag ? <span className="tag">{tag}</span> : null}
          </div>
        </div>
        <div
          className="body"
          style={{
            padding: "0 20px 18px",
            overflow: "auto",
            overscrollBehavior: "auto",
          }}
        >
          {children}
        </div>
      </div>
    </section>
  );
}

export function IdleNote({ label }: { label: string }) {
  return (
    <div className="idle-note">
      <svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.4">
        <circle cx="12" cy="12" r="9" />
        <path d="M12 7v5l3 2" />
      </svg>
      <span className="mono">{label}</span>
    </div>
  );
}

function weaveLinkLabel(url: string, fallback = "open eval"): string {
  try {
    const parsed = new URL(url);
    const search = parsed.searchParams.get("search");
    if (search) return search;
    const parts = parsed.pathname.split("/").filter(Boolean);
    const last = parts[parts.length - 1] || "";
    if (last && last.length <= 48 && !last.startsWith("%7B") && !last.includes("'output'")) {
      return last;
    }
    return fallback;
  } catch {
    return fallback;
  }
}

export function WeaveProofPanel({ proof }: { proof: WeaveProofView }) {
  return (
    <div className="weave-proof">
      <div className="weave-proof-head">
        <span className="weave-proof-title">W&B Weave</span>
        <span className={`weave-proof-badge${proof.enabled ? " on" : ""}`}>
          {proof.enabled ? "tracing on" : "tracing off"}
        </span>
      </div>
      <div className="weave-proof-links">
        <div className="weave-proof-row">
          <span className="weave-proof-label">Live traces</span>
          {proof.tracesUrl ? (
            <a className="weave-proof-link" href={proof.tracesUrl} target="_blank" rel="noreferrer">
              open Weave dashboard
            </a>
          ) : proof.enabled ? (
            <span className="weave-proof-muted">set WANDB_ENTITY for link</span>
          ) : (
            <span className="weave-proof-muted">tracing off</span>
          )}
        </div>
        <div className="weave-proof-row">
          <span className="weave-proof-label">Baseline</span>
          {proof.baselineUrl ? (
            <a className="weave-proof-link" href={proof.baselineUrl} target="_blank" rel="noreferrer">
              {weaveLinkLabel(proof.baselineUrl, proof.baselineLabel || "open baseline eval")}
            </a>
          ) : proof.baselineError ? (
            <span className="weave-proof-error">{proof.baselineError}</span>
          ) : (
            <span className="weave-proof-muted">pending baseline run</span>
          )}
        </div>
        <div className="weave-proof-row">
          <span className="weave-proof-label">Patched</span>
          {proof.patchedUrl ? (
            <a className="weave-proof-link" href={proof.patchedUrl} target="_blank" rel="noreferrer">
              {weaveLinkLabel(proof.patchedUrl, proof.patchedLabel || "open patched eval")}
            </a>
          ) : proof.patchedError ? (
            <span className="weave-proof-error">{proof.patchedError}</span>
          ) : (
            <span className="weave-proof-muted">pending patched rerun</span>
          )}
        </div>
      </div>
    </div>
  );
}

export function DemoBrief({ brief }: { brief: DemoBriefView }) {
  return (
    <section className="demoBrief">
      <div className="demoBriefCopy">
        <div className="demoEyebrow">3-minute story</div>
        <h2>{brief.headline}</h2>
        <p>{brief.subhead}</p>
        <div className="presenterLine">{brief.presenterLine}</div>
      </div>
      <div className="demoSteps" aria-label="Loopie demo proof path">
        {brief.steps.map((step, i) => (
          <div key={step.label} className={`demoStep ${step.status}`}>
            <span className="demoStepNum mono">{i + 1}</span>
            <span className="demoStepLabel">{step.label}</span>
          </div>
        ))}
      </div>
    </section>
  );
}

export function EventStream({ events }: { events: StreamEvent[] }) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (ref.current) ref.current.scrollTop = ref.current.scrollHeight;
  }, [events.length]);

  if (!events.length) return <IdleNote label="stream idle" />;

  return (
    <div className="stream-list" ref={ref}>
      <AnimatePresence initial={false}>
        {events.map((e) => (
          <motion.div
            key={e.seq}
            className={`ev ${e.level}`}
            layout
            initial={{ opacity: 0, x: -16, filter: "blur(4px)" }}
            animate={{ opacity: 1, x: 0, filter: "blur(0px)" }}
            transition={{ type: "spring", stiffness: 280, damping: 30 }}
          >
            <span className="evbar" />
            <span className="evnode">{e.node}</span>
            <span className="evmsg">{e.msg}</span>
          </motion.div>
        ))}
      </AnimatePresence>
    </div>
  );
}

export function FailedCase({ failure }: { failure: FailureView | null }) {
  if (!failure) return <IdleNote label="no case loaded" />;

  const scoreKeys = SCORE_ORDER.filter((k) => k in failure.scores);

  return (
    <motion.div variants={sceneStagger} initial="hidden" animate="show">
      <motion.div key="head" className="casehead" variants={riseIn}>
        <div className="casemeta">
          <div className="casechips">
            <span className="chip id">{failure.case_id}</span>
            <span className={`chip${failure.resolved ? " ok" : " fail"}`}>
              {failure.resolved ? "resolved" : failure.category}
            </span>
          </div>
          <div className="casetitle" style={{ marginTop: 8 }}>
            {failure.title}
          </div>
          {failure.resolved ? (
            <div className="caseinput" style={{ opacity: 0.72 }}>
              Archived baseline failure. The patched rerun recovered all scorers on {failure.case_id}.
            </div>
          ) : (
            <div className="caseinput">{failure.input}</div>
          )}
          <div className="errorBox" style={failure.resolved ? { opacity: 0.82 } : undefined}>
            <div>
              <span className="errorBoxLabel">Exact error</span>
              <p>{failure.exactError}</p>
            </div>
            <div className="whyFailedBox">
              <span className="errorBoxLabel">Root cause</span>
              <p>{failure.whyFailed}</p>
            </div>
            <div className="actionCompare">
              <div>
                <span className="errorBoxLabel">Swarm did</span>
                <strong>{failure.observedAction || "unknown"}</strong>
              </div>
              <div>
                <span className="errorBoxLabel">Should do</span>
                <strong>{failure.expectedAction || "see scorer"}</strong>
              </div>
            </div>
          </div>
        </div>
      </motion.div>
      <motion.div key="scores" className="scores" variants={riseIn}>
        {scoreKeys.map((k) => {
          const v = failure.scores[k];
          const low = v < 0.6;
          return (
            <div className="score" key={k}>
              <div className="scoretop">
                <span className="scorelabel">{k.replace(/_/g, " ")}</span>
                <span
                  className="scoreval"
                  style={{ color: low ? "var(--fail)" : "var(--teal)" }}
                >
                  <CountUp value={v} decimals={2} duration={1} />
                </span>
              </div>
              <div className="scoretrack">
                <motion.div
                  className="scorefill"
                  initial={{ width: 0 }}
                  animate={{ width: `${v * 100}%` }}
                  transition={{ duration: 1, ease: [0.16, 1, 0.3, 1] }}
                  style={{
                    background: low
                      ? "linear-gradient(90deg, rgba(255,90,90,.5), var(--fail))"
                      : "var(--aurora)",
                  }}
                />
              </div>
            </div>
          );
        })}
      </motion.div>
    </motion.div>
  );
}

export function CorrectionPanel({
  correction,
  onApprove,
  canApprove,
  loading,
}: {
  correction: CorrectionView | null;
  onApprove: () => void;
  canApprove: boolean;
  loading: boolean;
}) {
  const ripple = useRipple();

  if (!correction) return <IdleNote label="no correction proposed" />;

  return (
    <motion.div
      className="correction"
      variants={sceneStagger}
      initial="hidden"
      animate="show"
      key="corr"
    >
      <motion.div key="head" className="corrhead" variants={riseIn}>
        <div style={{ flex: 1 }}>
          <div className="corrtitle">{correction.title}</div>
          <div className="corrmeta">
            <span className="corrconf">
              conf {Math.round(correction.confidence * 100)}%
            </span>
            <span className="corrrisk">{correction.risk} risk</span>
            <span className="corrrisk">-&gt; {correction.target}</span>
          </div>
        </div>
      </motion.div>
      <motion.div key="rationale" className="corrrationale" variants={riseIn}>
        {correction.rationale}
      </motion.div>
      <motion.div key="decision" className="decisionBasis" variants={riseIn}>
        <span>How the fix was chosen</span>
        <p>{correction.decisionBasis}</p>
      </motion.div>
      <motion.div key="diff" className="diff" variants={riseIn}>
        <div className="diffhead">
          <span>Redis artifact diff</span>
          <span className="spacer" style={{ flex: 1 }} />
          <span>{correction.artifact}</span>
        </div>
        <div className="diffbody">
          {correction.diff.map((d, i) => (
            <motion.div
              key={i}
              className={`dline ${d.t}`}
              initial={{ opacity: 0, x: d.t === "add" ? 12 : d.t === "del" ? -12 : 0 }}
              animate={{ opacity: 1, x: 0 }}
              transition={{ delay: 0.3 + i * 0.07, duration: 0.3 }}
            >
              <span className="gutter">{d.t === "add" ? "+" : d.t === "del" ? "-" : ""}</span>
              <span className="code">{d.l}</span>
            </motion.div>
          ))}
        </div>
      </motion.div>
      <motion.div key="blast" variants={riseIn} className="blastwrap">
        <div className="subhead">Blast radius</div>
        <BlastRadius blast={correction.blast} target={correction.target} />
      </motion.div>
      <motion.div key="approve" className="approvebar" variants={riseIn}>
        {correction.approved ? (
          <div className="approve-status">
            <motion.span
              className="approve-check"
              initial={{ scale: 0 }}
              animate={{ scale: 1 }}
              transition={{ type: "spring", stiffness: 300, damping: 16 }}
            >
              <svg viewBox="0 0 24 24" fill="none" stroke="#08080e" strokeWidth="3">
                <path d="M5 13l4 4L19 7" />
              </svg>
            </motion.span>
            <span>Approved / staged - ready to re-run</span>
          </div>
        ) : (
          <>
            <span className="approval-note">human gate</span>
            <span className="grow" />
            <button
              type="button"
              className="cmd armed"
              disabled={!canApprove || loading}
              onClick={(e) => {
                ripple(e);
                onApprove();
              }}
            >
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4">
                <path d="M5 13l4 4L19 7" />
              </svg>
              Approve correction
            </button>
          </>
        )}
      </motion.div>
    </motion.div>
  );
}
