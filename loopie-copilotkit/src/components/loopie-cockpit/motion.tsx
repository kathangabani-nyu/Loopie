"use client";

import { animate, useReducedMotion } from "framer-motion";
import { useEffect, useRef, useState, type MouseEvent } from "react";

export const sceneStagger = {
  hidden: {},
  show: { transition: { staggerChildren: 0.07, delayChildren: 0.05 } },
  exit: { transition: { staggerChildren: 0.03, staggerDirection: -1 } },
};

export const riseIn = {
  hidden: { opacity: 0, y: 16, filter: "blur(6px)" },
  show: {
    opacity: 1,
    y: 0,
    filter: "blur(0px)",
    transition: { type: "spring" as const, stiffness: 230, damping: 26 },
  },
  exit: { opacity: 0, y: -10, filter: "blur(4px)", transition: { duration: 0.25 } },
};

export function CountUp({
  value,
  decimals = 0,
  duration = 1.1,
  suffix = "",
  prefix = "",
  className,
}: {
  value: number;
  decimals?: number;
  duration?: number;
  suffix?: string;
  prefix?: string;
  className?: string;
}) {
  const reduce = useReducedMotion();
  const [display, setDisplay] = useState(reduce ? value : 0);
  const ref = useRef(0);

  useEffect(() => {
    if (reduce) {
      setDisplay(value);
      return;
    }
    const controls = animate(ref.current, value, {
      duration,
      ease: [0.16, 1, 0.3, 1],
      onUpdate: (v) => {
        ref.current = v;
        setDisplay(v);
      },
    });
    return () => controls.stop();
  }, [value, reduce, duration]);

  const txt = Number(display).toLocaleString("en-US", {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  });

  return (
    <span className={className}>
      {prefix}
      {txt}
      {suffix}
    </span>
  );
}

export function useRipple() {
  return (e: MouseEvent<HTMLElement>) => {
    const btn = e.currentTarget;
    const r = document.createElement("span");
    r.className = "ripple";
    const rect = btn.getBoundingClientRect();
    const size = Math.max(rect.width, rect.height);
    r.style.width = `${size}px`;
    r.style.height = `${size}px`;
    r.style.left = `${e.clientX - rect.left}px`;
    r.style.top = `${e.clientY - rect.top}px`;
    btn.appendChild(r);
    setTimeout(() => r.remove(), 650);
  };
}
