// Appearance context — brand + density.
// Sets [data-brand] and [data-density] on <html> so the token CSS can switch.
// Brand choices: 'bv' (Better Vision red) | 'wizopt' (WizOpt teal).
// Density: 'default' | 'compact'.

import { createContext, useContext, useEffect, useState } from 'react';
import type { ReactNode } from 'react';

export type Brand = 'bv' | 'wizopt';
export type Density = 'default' | 'compact';

interface AppearanceContextType {
  brand: Brand;
  density: Density;
  /** Sidebar rail mode — false = icon-only (64px), true = icon + label (200px). */
  railExpanded: boolean;
  setBrand: (b: Brand) => void;
  setDensity: (d: Density) => void;
  setRailExpanded: (v: boolean) => void;
  toggleRailExpanded: () => void;
}

const AppearanceContext = createContext<AppearanceContextType | undefined>(undefined);

const BRAND_KEY = 'ims.brand';
const DENSITY_KEY = 'ims.density';
const RAIL_KEY = 'ims.rail-expanded';

function readBrand(): Brand {
  if (typeof localStorage === 'undefined') return 'bv';
  const v = localStorage.getItem(BRAND_KEY);
  return v === 'wizopt' ? 'wizopt' : 'bv';
}

function readDensity(): Density {
  if (typeof localStorage === 'undefined') return 'default';
  const v = localStorage.getItem(DENSITY_KEY);
  return v === 'compact' ? 'compact' : 'default';
}

function readRailExpanded(): boolean {
  if (typeof localStorage === 'undefined') return false;
  return localStorage.getItem(RAIL_KEY) === '1';
}

export function AppearanceProvider({ children }: { children: ReactNode }) {
  const [brand, setBrandState] = useState<Brand>(readBrand);
  const [density, setDensityState] = useState<Density>(readDensity);
  const [railExpanded, setRailExpandedState] = useState<boolean>(readRailExpanded);

  useEffect(() => {
    const root = document.documentElement;
    root.setAttribute('data-brand', brand);
  }, [brand]);

  useEffect(() => {
    const root = document.documentElement;
    if (density === 'compact') root.setAttribute('data-density', 'compact');
    else root.removeAttribute('data-density');
  }, [density]);

  const setBrand = (b: Brand) => {
    setBrandState(b);
    try { localStorage.setItem(BRAND_KEY, b); } catch { /* storage may be blocked */ }
  };
  const setDensity = (d: Density) => {
    setDensityState(d);
    try { localStorage.setItem(DENSITY_KEY, d); } catch { /* storage may be blocked */ }
  };
  const setRailExpanded = (v: boolean) => {
    setRailExpandedState(v);
    try { localStorage.setItem(RAIL_KEY, v ? '1' : '0'); } catch { /* storage may be blocked */ }
  };
  const toggleRailExpanded = () => setRailExpanded(!railExpanded);

  return (
    <AppearanceContext.Provider value={{
      brand, density, railExpanded,
      setBrand, setDensity, setRailExpanded, toggleRailExpanded,
    }}>
      {children}
    </AppearanceContext.Provider>
  );
}

export function useAppearance(): AppearanceContextType {
  const ctx = useContext(AppearanceContext);
  if (!ctx) throw new Error('useAppearance must be used within AppearanceProvider');
  return ctx;
}
