// ============================================================================
// IMS 2.0 - Theme Toggle Component
// ============================================================================
// Button component for switching between light/dark/system theme modes

import { Moon, Sun, Smartphone } from 'lucide-react';
import clsx from 'clsx';
import { useTheme, type ThemeMode } from '../../context/ThemeContext';

export function ThemeToggle() {
  const { mode, setMode } = useTheme();

  const themes: { mode: ThemeMode; label: string; icon: React.ReactNode }[] = [
    { mode: 'light', label: 'Light', icon: <Sun className="w-4 h-4" /> },
    { mode: 'dark', label: 'Dark', icon: <Moon className="w-4 h-4" /> },
    { mode: 'system', label: 'System', icon: <Smartphone className="w-4 h-4" /> },
  ];

  return (
    <div className="flex items-center gap-1 p-1 bg-gray-100 dark:bg-gray-800 rounded-lg">
      {themes.map(theme => (
        <button
          key={theme.mode}
          onClick={() => setMode(theme.mode)}
          className={clsx(
            'flex items-center gap-1 px-3 py-1.5 rounded transition-colors',
            mode === theme.mode
              ? 'bg-white dark:bg-gray-700 text-gray-900 dark:text-white shadow-sm'
              : 'text-gray-600 dark:text-gray-400 hover:text-gray-900 dark:hover:text-white'
          )}
          title={`Switch to ${theme.label} mode`}
          aria-label={`Switch to ${theme.label} theme`}
          aria-pressed={mode === theme.mode}
        >
          {theme.icon}
          <span className="text-xs font-medium hidden sm:inline">{theme.label}</span>
        </button>
      ))}
    </div>
  );
}

/**
 * Simplified toggle button (light/dark only)
 */
export function SimpleThemeToggle() {
  const { isDark, toggleDarkMode } = useTheme();

  return (
    <button
      onClick={toggleDarkMode}
      className={clsx(
        'p-2 rounded-lg transition-colors',
        isDark
          ? 'bg-gray-800 text-yellow-400 hover:bg-gray-700'
          : 'bg-gray-100 text-gray-600 hover:bg-gray-200'
      )}
      title={`Switch to ${isDark ? 'light' : 'dark'} mode`}
      aria-label={`Switch to ${isDark ? 'light' : 'dark'} theme`}
      aria-pressed={isDark}
    >
      {isDark ? <Sun className="w-5 h-5" /> : <Moon className="w-5 h-5" />}
    </button>
  );
}
