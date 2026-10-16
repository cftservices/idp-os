"use client";

/**
 * PBR-texturen (ambientCG, CC0) voor vloer, wanden en gelakte kasten.
 * Laden zonder suspense: tot een textuur binnen is, rendert het materiaal vlak.
 */
import { useEffect, useState } from "react";
import { LinearSRGBColorSpace, RepeatWrapping, SRGBColorSpace, type Texture, TextureLoader } from "three";

const BASE = "/models/site/tex/";
const cache = new Map<string, Promise<Texture>>();
const loader = typeof window !== "undefined" ? new TextureLoader() : null;

function load(name: string, color: boolean): Promise<Texture> {
  const key = `${name}|${color}`;
  let p = cache.get(key);
  if (!p && loader) {
    p = loader.loadAsync(`${BASE}${name}.webp`).then((t) => {
      t.wrapS = t.wrapT = RepeatWrapping;
      t.colorSpace = color ? SRGBColorSpace : LinearSRGBColorSpace;
      t.anisotropy = 8;
      return t;
    });
    cache.set(key, p);
  }
  return p ?? Promise.reject(new Error("geen loader"));
}

export interface PbrSet {
  map?: Texture;
  normalMap?: Texture;
  roughnessMap?: Texture;
}

/** Textuurset per gebruik gekloond met eigen herhaling. */
export function usePbr(base: "beton" | "golfplaat" | "lak", repeat: [number, number] = [1, 1]): PbrSet {
  const [set, setSet] = useState<PbrSet>({});
  const [rx, ry] = repeat;
  useEffect(() => {
    let gone = false;
    const maps: [keyof PbrSet, string, boolean][] = [
      ["normalMap", `${base}-normaal`, false],
      ["roughnessMap", `${base}-ruw`, false],
    ];
    if (base !== "lak") maps.push(["map", `${base}-kleur`, true]);
    Promise.all(maps.map(([, n, c]) => load(n, c)))
      .then((tex) => {
        if (gone) return;
        const out: PbrSet = {};
        tex.forEach((t, i) => {
          const c = t.clone();
          c.repeat.set(rx, ry);
          c.needsUpdate = true;
          out[maps[i][0]] = c;
        });
        setSet(out);
      })
      .catch(() => undefined);
    return () => {
      gone = true;
    };
  }, [base, rx, ry]);
  return set;
}
