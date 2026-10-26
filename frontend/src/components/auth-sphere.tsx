"use client";
import dynamic from "next/dynamic";
import { motion } from "motion/react";

const Armillary = dynamic(() => import("./armillary"), { ssr: false });

export function AuthSphere() {
  return (
    <motion.div className="absolute inset-0" initial={{ opacity: 0, scale: 0.92 }} animate={{ opacity: 1, scale: 1 }}
      transition={{ duration: 1.1, ease: [0.2, 0.8, 0.2, 1] }}>
      <motion.div className="h-full w-full" animate={{ y: [0, -14, 0] }} transition={{ duration: 7, repeat: Infinity, ease: "easeInOut" }}>
        <Armillary className="h-full w-full" speed={0.8} />
      </motion.div>
    </motion.div>
  );
}
