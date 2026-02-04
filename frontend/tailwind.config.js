/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        // Better Vision Brand Colors (Gold/Brown - from bettervision.in)
        'bv-gold': {
          50: '#fdfaf7',
          100: '#f9f0e6',
          200: '#f2dcc8',
          300: '#e8c3a0',
          400: '#d4a373',
          500: '#ba8659', // Primary Better Vision Gold
          600: '#a67547',
          700: '#8b5e3a',
          800: '#6f4a2f',
          900: '#533724',
          950: '#2d1d14',
        },
        // Legacy alias for backwards compatibility
        'bv-red': {
          50: '#fdfaf7',
          100: '#f9f0e6',
          200: '#f2dcc8',
          300: '#e8c3a0',
          400: '#d4a373',
          500: '#ba8659',
          600: '#a67547', // Primary (was #CD201A)
          700: '#8b5e3a',
          800: '#6f4a2f',
          900: '#533724',
          950: '#2d1d14',
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
        sans: ['Nunito', 'Inter', 'system-ui', 'sans-serif'],
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
