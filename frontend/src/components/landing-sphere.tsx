"use client";
import dynamic from "next/dynamic";
import { motion, useReducedMotion, useScroll, useSpring, useTransform } from "motion/react";

const Armillary = dynamic(() => import("./armillary"), { ssr: false });

/** The sphere travels across the page as you scroll: right → left → top-right → bottom-left → centre. */
export function LandingSphere() {
  const reduce = useReducedMotion();
  const { scrollYProgress } = useScroll();
  const p = useSpring(scrollYProgress, { stiffness: 80, damping: 22, mass: 0.6 });
  const stops = [0, 0.22, 0.45, 0.68, 0.9, 1];
  const x = useTransform(p, stops, ["28vw", "-30vw", "26vw", "-26vw", "0vw", "0vw"]);
  const y = useTransform(p, stops, ["4vh", "10vh", "-22vh", "18vh", "-6vh", "-6vh"]);
  const scale = useTransform(p, stops, [1, 0.72, 0.6, 0.8, 1.1, 1.1]);
  const rotate = useTransform(p, [0, 1], [0, 140]);
  const opacity = useTransform(p, [0, 0.05, 0.95, 1], [0.95, 0.85, 0.85, 0.95]);
  return (
    <motion.div aria-hidden className="pointer-events-none fixed left-1/2 top-1/2 -z-[5] size-[min(78vw,640px)] -translate-x-1/2 -translate-y-1/2"
      style={reduce ? undefined : { x, y, scale, rotate, opacity }}>
      <Armillary className="h-full w-full" />
    </motion.div>
  );
}
