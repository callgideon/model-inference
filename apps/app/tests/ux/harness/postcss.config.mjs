// The App's Tailwind, scanning the App's own sources for class names: tests/ux/browser.ts passes the
// App directory (the PostCSS worker's working directory is not it, and this file cannot use import.meta).
const config = {
  plugins: {
    "@tailwindcss/postcss": { base: process.env.INFRX_UX_APP_DIR },
  },
};

export default config;
