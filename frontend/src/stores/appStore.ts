// ============================================================================
// IMS 2.0 - Application State Store (Zustand)
// ============================================================================
// Centralized state management using Zustand with proper ES6 module export
// Using named import `{ create }` instead of deprecated default export

import { create } from 'zustand';

// ============================================================================
// Types
// ============================================================================

export interface AppState {
  // UI State
  sidebarOpen: boolean;
  setSidebarOpen: (open: boolean) => void;

  // Theme State
  darkMode: boolean;
  setDarkMode: (dark: boolean) => void;

  // Loading State
  isLoading: boolean;
  setIsLoading: (loading: boolean) => void;

  // Current Module State
  currentModule: string | null;
  setCurrentModule: (module: string | null) => void;

  // Filter State
  filters: Record<string, any>;
  setFilters: (filters: Record<string, any>) => void;
  clearFilters: () => void;

  // Reset all state
  reset: () => void;
}

// ============================================================================
// Initial State
// ============================================================================

const initialState = {
  sidebarOpen: true,
  darkMode: false,
  isLoading: false,
  currentModule: null,
  filters: {},
};

// ============================================================================
// Zustand Store
// ============================================================================

export const useAppStore = create<AppState>((set) => ({
  ...initialState,

  setSidebarOpen: (open) => set({ sidebarOpen: open }),

  setDarkMode: (dark) => set({ darkMode: dark }),

  setIsLoading: (loading) => set({ isLoading: loading }),

  setCurrentModule: (module) => set({ currentModule: module }),

  setFilters: (filters) => set({ filters }),

  clearFilters: () => set({ filters: {} }),

  reset: () => set(initialState),
}));

// ============================================================================
// Store Selectors (Optional - for performance optimization)
// ============================================================================

export const selectSidebarOpen = (state: AppState) => state.sidebarOpen;
export const selectDarkMode = (state: AppState) => state.darkMode;
export const selectIsLoading = (state: AppState) => state.isLoading;
export const selectCurrentModule = (state: AppState) => state.currentModule;
export const selectFilters = (state: AppState) => state.filters;
