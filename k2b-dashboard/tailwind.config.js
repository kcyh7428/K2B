/** @type {import('tailwindcss').Config} */
export default {
  content: ['./src/client/**/*.{ts,tsx,html}'],
  theme: {
    extend: {
      colors: {
        bg: {
          base: '#0a0a0b',
          panel: '#141417',
          raised: '#1c1c20',
          border: '#26262c',
        },
        ink: {
          primary: '#f5f5f7',
          secondary: '#a1a1a8',
          muted: '#6b6b74',
        },
        accent: {
          green: '#4ade80',
          amber: '#fbbf24',
          red: '#f87171',
          blue: '#60a5fa',
        },
      },
      fontFamily: {
        mono: ['ui-monospace', 'SFMono-Regular', 'Menlo', 'monospace'],
      },
    },
  },
  plugins: [],
}
