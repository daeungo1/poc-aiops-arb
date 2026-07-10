/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        'azure-blue': '#0078d4',
        'azure-dark': '#004578',
        'azure-light': '#deecf9',
        'azure-lighter': '#eff6fc',
        'status-pass': '#107c10',
        'status-warn': '#ff8c00',
        'status-fail': '#d13438',
      },
    },
  },
  plugins: [],
}
