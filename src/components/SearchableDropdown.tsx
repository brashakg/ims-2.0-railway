'use client';

import { useState, useRef, useEffect } from 'react';
import { ChevronDown, X } from 'lucide-react';

interface SearchableDropdownProps {
  options: string[];
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  label?: string;
  disabled?: boolean;
  /**
   * Visual size. 'md' uses base-size text and 44px min-height for
   * variant-manager and mobile-friendly forms where characters must be
   * fully visible. Default 'sm' keeps the compact filter-bar look. */
  size?: 'sm' | 'md';
}

export default function SearchableDropdown({
  options,
  value,
  onChange,
  placeholder = 'Select...',
  label,
  disabled = false,
  size = 'sm',
}: SearchableDropdownProps) {
  const textCls = size === 'md' ? 'text-base' : 'text-sm';
  const padCls = size === 'md' ? 'px-3 py-2.5 min-h-[44px]' : 'px-3 py-2';
  const [isOpen, setIsOpen] = useState(false);
  const [search, setSearch] = useState('');
  const [filteredOptions, setFilteredOptions] = useState(options);
  const containerRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const filtered = options.filter((option) =>
      option.toLowerCase().includes(search.toLowerCase())
    );
    setFilteredOptions(filtered);
  }, [search, options]);

  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (
        containerRef.current &&
        !containerRef.current.contains(event.target as Node)
      ) {
        setIsOpen(false);
        setSearch('');
      }
    }

    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  const handleSelect = (option: string) => {
    onChange(option);
    setIsOpen(false);
    setSearch('');
  };

  const handleClear = (e: React.MouseEvent) => {
    e.stopPropagation();
    onChange('');
    setSearch('');
  };

  const displayValue = value || placeholder;

  return (
    <div className="relative w-full" ref={containerRef}>
      {label && (
        <label className="block text-sm font-medium text-gray-700 mb-1">
          {label}
        </label>
      )}
      <div
        className={`relative border rounded-lg bg-white transition-colors ${
          disabled
            ? 'bg-gray-50 cursor-not-allowed'
            : 'hover:border-gray-400 cursor-pointer'
        } ${isOpen ? 'border-blue-500 ring-1 ring-blue-500' : 'border-gray-300'}`}
        onClick={() => !disabled && setIsOpen(!isOpen)}
      >
        <div className={`flex items-center justify-between ${padCls}`}>
          {isOpen ? (
            <input
              ref={inputRef}
              type="text"
              placeholder={placeholder}
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className={`flex-1 outline-none ${textCls}`}
              onClick={(e) => e.stopPropagation()}
              disabled={disabled}
              autoFocus
            />
          ) : (
            <span
              className={`${textCls} flex-1 truncate ${
                !value ? 'text-gray-500' : 'text-gray-900'
              }`}
            >
              {displayValue}
            </span>
          )}
          <div className="flex items-center gap-2 ml-2">
            {value && !disabled && (
              <button
                onClick={handleClear}
                className="p-1 hover:bg-gray-100 rounded transition-colors"
              >
                <X className="w-4 h-4 text-gray-500" />
              </button>
            )}
            <ChevronDown
              className={`w-4 h-4 text-gray-400 transition-transform ${
                isOpen ? 'transform rotate-180' : ''
              }`}
            />
          </div>
        </div>
      </div>

      {isOpen && (
        <div className="absolute top-full left-0 right-0 mt-1 bg-white border border-gray-300 rounded-lg shadow-lg z-50 max-h-64 overflow-y-auto">
          {filteredOptions.length > 0 ? (
            <ul className="py-1">
              {filteredOptions.map((option) => (
                <li key={option}>
                  <button
                    onClick={() => handleSelect(option)}
                    className={`w-full text-left px-3 py-2 text-sm transition-colors ${
                      value === option
                        ? 'bg-blue-50 text-blue-600 font-medium'
                        : 'text-gray-900 hover:bg-gray-100'
                    }`}
                  >
                    {option}
                  </button>
                </li>
              ))}
            </ul>
          ) : (
            <div className="px-3 py-2 text-sm text-gray-500 text-center">
              No options found
            </div>
          )}
        </div>
      )}
    </div>
  );
}
