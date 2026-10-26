"use client";
/** Procedural armillary sphere: meridian, equator, ecliptic and colure rings around a glowing core. */
import { Canvas, useFrame } from "@react-three/fiber";
import { useMemo, useRef } from "react";
import * as THREE from "three";

function Rings({ speed = 1 }: { speed?: number }) {
  const group = useRef<THREE.Group>(null);
  const inner = useRef<THREE.Group>(null);
  const bead = useRef<THREE.Mesh>(null);
  const gold = useMemo(() => new THREE.MeshStandardMaterial({ color: "#d9b46a", metalness: 0.85, roughness: 0.3 }), []);
  const violet = useMemo(() => new THREE.MeshStandardMaterial({ color: "#9b86ff", metalness: 0.6, roughness: 0.35, emissive: "#2a1f70", emissiveIntensity: 0.4 }), []);
  const ticks = useMemo(() => Array.from({ length: 24 }, (_, i) => (i / 24) * Math.PI * 2), []);
  useFrame((_, dt) => {
    if (group.current) group.current.rotation.y += dt * 0.18 * speed;
    if (inner.current) inner.current.rotation.z += dt * 0.35 * speed;
    if (bead.current) {
      const a = performance.now() / 1000 * 0.8 * speed;
      bead.current.position.set(Math.cos(a) * 1.55, Math.sin(a) * 1.55 * Math.sin(0.41), Math.sin(a) * 1.55 * Math.cos(0.41));
    }
  });
  return (
    <group ref={group} rotation={[0.35, 0, -0.2]}>
      <mesh material={gold}><torusGeometry args={[2, 0.035, 16, 160]} /></mesh>
      <mesh material={gold} rotation={[Math.PI / 2, 0, 0]}><torusGeometry args={[2, 0.03, 16, 160]} /></mesh>
      <mesh material={gold} rotation={[0, Math.PI / 2, 0]}><torusGeometry args={[2, 0.025, 16, 160]} /></mesh>
      {ticks.map((a) => (
        <mesh key={a} material={gold} position={[Math.cos(a) * 2, 0, Math.sin(a) * 2]} rotation={[0, -a, 0]}>
          <boxGeometry args={[0.02, 0.12, 0.02]} />
        </mesh>
      ))}
      <group ref={inner} rotation={[0.41, 0, 0]}>
        <mesh material={violet} rotation={[Math.PI / 2, 0, 0]}><torusGeometry args={[1.55, 0.06, 16, 140]} /></mesh>
        <mesh material={violet}><torusGeometry args={[1.25, 0.02, 12, 120]} /></mesh>
      </group>
      <mesh ref={bead}><sphereGeometry args={[0.09, 24, 24]} /><meshStandardMaterial color="#7fe3d4" emissive="#7fe3d4" emissiveIntensity={1.4} /></mesh>
      <mesh><sphereGeometry args={[0.42, 48, 48]} /><meshStandardMaterial color="#ffd9a8" emissive="#ffb070" emissiveIntensity={1.1} /></mesh>
      <mesh material={gold} rotation={[0, 0, 0.41]}><cylinderGeometry args={[0.018, 0.018, 5.2, 12]} /></mesh>
    </group>
  );
}

export default function Armillary({ speed = 1, className = "" }: { speed?: number; className?: string }) {
  const reduce = typeof window !== "undefined" && (window.matchMedia("(prefers-reduced-motion: reduce)").matches || document.documentElement.dataset.motion === "off");
  return (
    <div className={className} aria-hidden>
      <Canvas camera={{ position: [0, 0.6, 6.2], fov: 42 }} dpr={[1, 1.75]} frameloop={reduce ? "demand" : "always"} gl={{ antialias: true, alpha: true }}>
        <ambientLight intensity={0.35} />
        <pointLight position={[0, 0, 0]} intensity={18} color="#ffb070" />
        <directionalLight position={[4, 5, 3]} intensity={1.6} />
        <directionalLight position={[-5, -2, -4]} intensity={0.5} color="#7f8cff" />
        <Rings speed={reduce ? 0 : speed} />
      </Canvas>
    </div>
  );
}
