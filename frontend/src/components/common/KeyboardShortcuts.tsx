// ============================================================================
// IMS 2.0 - Keyboard Shortcuts Component
// ============================================================================
// Global keyboard shortcut handler and shortcuts reference modal

import { useEffect, useState } from 'react';
import { X, Command } from 'lucide-react';

export interface Shortcut {
  key: string; // e.g., 'ctrl+k', 'cmd+shift+s', 'escape'
  label: string;
  description: string;
  callback: () => void;
  category: string;
}

export function useKeyboardShortcuts(shortcuts: Shortcut[]) {
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      // Ignore if typing in input/textarea
      const target = e.target as HTMLElement;
      if (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA') {
        if (!(e.ctrlKey || e.metaKey) || e.key.toLowerCase() !== 'k') return; // Allow Ctrl+K everywhere
      }

      const keys = [];
      if (e.ctrlKey || e.metaKey) keys.push(e.metaKey ? 'cmd' : 'ctrl');
      if (e.shiftKey) keys.push('shift');
      if (e.altKey) keys.push('alt');
      keys.push(e.key.toLowerCase());

      const pressedKey = keys.join('+');

      const matchedShortcut = shortcuts.find(s => {
        const shortcutKeys = s.key.toLowerCase().split('+');
        return shortcutKeys.join('+') === pressedKey;
      });

      if (matchedShortcut) {
        e.preventDefault();
        matchedShortcut.callback();
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [shortcuts]);
}

export function KeyboardShortcutsModal() {
  const [showModal, setShowModal] = useState(false);

  const defaultShortcuts: Shortcut[] = [
    {
      key: 'ctrl+k',
      label: 'Ctrl + K',
      description: 'Open command palette / search',
      callback: () => console.log('Command palette opened'),
      category: 'Navigation',
    },
    {
      key: 'ctrl+shift+s',
      label: 'Ctrl + Shift + S',
      description: 'Save current page',
      callback: () => alert('Save functionality'),
      category: 'Editing',
    },
    {
      key: 'ctrl+h',
      label: 'Ctrl + H',
      description: 'Open help / keyboard shortcuts',
      callback: () => setShowModal(true),
      category: 'Help',
    },
    {
      key: 'escape',
      label: 'Escape',
      description: 'Close modal / clear selection',
      callback: () => setShowModal(false),
      category: 'Navigation',
    },
  ];

  // Register shortcuts
  useKeyboardShortcuts(defaultShortcuts);

  // Show modal on Ctrl+H
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'h') {
        e.preventDefault();
        setShowModal(true);
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, []);

  const groupedShortcuts = defaultShortcuts.reduce((acc, shortcut) => {
    if (!acc[shortcut.category]) {
      acc[shortcut.category] = [];
    }
    acc[shortcut.category].push(shortcut);
    return acc;
  }, {} as Record<string, Shortcut[]>);

  if (!showModal) return null;

  return (
    <div
      className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50"
      onClick={() => setShowModal(false)}
    >
      <div
        className="bg-white dark:bg-gray-900 rounded-lg shadow-xl max-w-2xl w-full max-h-96 overflow-y-auto"
        onClick={e => e.stopPropagation()}
        role="dialog"
        aria-labelledby="shortcuts-title"
      >
        {/* Header */}
        <div className="sticky top-0 flex items-center justify-between p-4 border-b border-gray-200 dark:border-gray-800 bg-white dark:bg-gray-900">
          <div className="flex items-center gap-2">
            <Command className="w-5 h-5 text-gray-600 dark:text-gray-400" />
            <h2 id="shortcuts-title" className="text-lg font-bold text-gray-900 dark:text-white">
              Keyboard Shortcuts
            </h2>
          </div>
          <button
            onClick={() => setShowModal(false)}
            className="p-2 hover:bg-gray-100 dark:hover:bg-gray-800 rounded-lg transition-colors"
            aria-label="Close"
          >
            <X className="w-5 h-5 text-gray-400" />
          </button>
        </div>

        {/* Content */}
        <div className="p-4 space-y-6">
          {Object.entries(groupedShortcuts).map(([category, shortcuts]) => (
            <div key={category}>
              <h3 className="text-sm font-semibold text-gray-700 dark:text-gray-300 mb-3 uppercase tracking-wide">
                {category}
              </h3>
              <div className="space-y-2">
                {shortcuts.map((shortcut, i) => (
                  <div
                    key={i}
                    className="flex items-center justify-between p-3 rounded-lg bg-gray-50 dark:bg-gray-800 hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors cursor-pointer"
                    onClick={() => {
                      shortcut.callback();
                      setShowModal(false);
                    }}
                  >
                    <div>
                      <p className="text-sm font-medium text-gray-900 dark:text-white">
                        {shortcut.description}
                      </p>
                    </div>
                    <div className="flex items-center gap-1">
                      {shortcut.key.split('+').map((key, j) => (
                        <div key={j} className="flex items-center gap-1">
                          {j > 0 && <span className="text-gray-400 text-xs">+</span>}
                          <kbd className="px-2 py-1 text-xs font-semibold text-gray-900 dark:text-white bg-white dark:bg-gray-700 border border-gray-200 dark:border-gray-600 rounded">
                            {key.charAt(0).toUpperCase() + key.slice(1)}
                          </kbd>
                        </div>
                      ))}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>

        {/* Footer */}
        <div className="sticky bottom-0 p-4 border-t border-gray-200 dark:border-gray-800 bg-white dark:bg-gray-900">
          <p className="text-xs text-gray-500 dark:text-gray-400">
            Press <kbd className="px-1.5 py-0.5 bg-gray-100 dark:bg-gray-800 border border-gray-300 dark:border-gray-700 rounded text-xs">Escape</kbd> to close
          </p>
        </div>
      </div>
    </div>
  );
}

/**
 * Keyboard shortcut handler hook for custom shortcuts
 */
export function useCustomShortcut(key: string, callback: () => void, enabled = true) {
  useEffect(() => {
    if (!enabled) return;

    const handleKeyDown = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement;
      if (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA') return;

      const keys = [];
      if (e.ctrlKey || e.metaKey) keys.push(e.metaKey ? 'cmd' : 'ctrl');
      if (e.shiftKey) keys.push('shift');
      if (e.altKey) keys.push('alt');
      keys.push(e.key.toLowerCase());

      if (keys.join('+') === key.toLowerCase()) {
        e.preventDefault();
        callback();
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [key, callback, enabled]);
}
