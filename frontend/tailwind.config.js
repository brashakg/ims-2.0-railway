/** @type {import('tailwindcss').Config} */
export default {
  darkMode: 'class',
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        // Better Vision Brand (BV red — gold scale retained as alias for
        // backwards compat; both now point at the new BV red palette).
        'bv-gold': {
          50:  '#fbe8e6',
          100: '#fad1cf',
          200: '#f5a8a4',
          300: '#eb7b75',
          400: '#dd4c45',
          500: '#CD201A',
          600: '#B81A15',
          700: '#9e1611',
          800: '#7d110d',
          900: '#5d0d0a',
          950: '#3e0806',
        },
        'bv-red': {
          50:  '#fbe8e6',
          100: '#fad1cf',
          200: '#f5a8a4',
          300: '#eb7b75',
          400: '#dd4c45',
          500: '#CD201A',
          600: '#B81A15',
          700: '#9e1611',
          800: '#7d110d',
          900: '#5d0d0a',
          950: '#3e0806',
        },
        // WizOpt Brand Colors (Teal/Blue)
        'wz-teal': {
          50: '#f0fdfa',
          100: '#ccfbf1',
          200: '#99f6e4',
          300: '#5eead4',
          400: '#2dd4bf',
          500: '#14b8a6',
          600: '#0d9488', // Primary WizOpt Teal
          700: '#0f766e',
          800: '#115e59',
          900: '#134e4a',
          950: '#042f2e',
        },
        // Status Colors
        'success': '#10B981',
        'warning': '#F59E0B',
        'error': '#EF4444',
        'info': '#3B82F6',
      },
      fontFamily: {
        sans: ['Inter', 'ui-sans-serif', 'system-ui', '-apple-system', 'Segoe UI', 'Roboto', 'sans-serif'],
        display: ['"Instrument Serif"', 'Times New Roman', 'serif'],
        mono: ['"JetBrains Mono"', 'ui-monospace', 'Menlo', 'monospace'],
        optician: ['"Optician Sans"', 'monospace'],
      },
      screens: {
        // Tablet-first breakpoints
        'tablet': '768px',
        'laptop': '1024px',
        'desktop': '1280px',
      },
      spacing: {
        '18': '4.5rem',
        '88': '22rem',
        '128': '32rem',
      },
    },
  },
  plugins: [],
}
