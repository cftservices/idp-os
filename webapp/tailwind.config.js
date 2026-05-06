/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    './app/**/*.{js,ts,jsx,tsx}',
    './lib/**/*.{js,ts}',
  ],
  theme: {
    extend: {
      colors: {
        navy: {
          950: '#0a0f1e',
          900: '#0d1424',
          800: '#111827',
          700: '#1a2235',
        },
      },
      fontFamily: {
        mono: ['JetBrains Mono', 'Menlo', 'monospace'],
      },
    },
  },
  plugins: [],
};
