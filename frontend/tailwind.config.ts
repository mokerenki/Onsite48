// frontend/tailwind.config.ts

import type { Config } from "tailwindcss";

const config: Config = {
  content: [
    "./src/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      colors: {
        brand: {
          50: '#f8f8f6',
          100: '#efefeb',
          200: '#deddd6',
          300: '#c7c5bb',
          400: '#9a978c',
          500: '#6f6b61',
          600: '#4d4a43',
          700: '#3b3933',
          800: '#34322d',
          900: '#25231f',
          950: '#171613',
        },
        onsite48: {
          background: '#ffffff',
          surface: '#f8f8f6',
          'surface-light': '#f2f2ef',
          'surface-hover': '#ebeae5',
          border: '#e5e3dc',
          text: '#34322d',
          'text-secondary': '#5c594f',
          'text-muted': '#8f897c',
        },
        text: {
          primary: '#34322d',
          secondary: '#5c594f',
          muted: '#8f897c',
        },
      },
      fontFamily: {
        sans: ['Inter', 'system-ui', 'sans-serif'],
      },
      backgroundImage: {
        'brand-gradient': 'linear-gradient(135deg, #34322d, #171613)',
        'brand-gradient-subtle': 'linear-gradient(135deg, rgba(52,50,45,0.08), rgba(23,22,19,0.04))',
      },
      boxShadow: {
        'card': '0 1px 3px rgba(0,0,0,0.06), 0 1px 2px rgba(0,0,0,0.04)',
        'card-hover': '0 4px 12px rgba(0,0,0,0.08)',
      },
    },
  },
  plugins: [],
};

export default config;