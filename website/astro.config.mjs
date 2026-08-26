import { defineConfig } from 'astro/config';
import tailwindcss from '@tailwindcss/vite';

export default defineConfig({
  site: 'https://www.draftomen.com',
  output: 'static',
  outDir: './dist',
  vite: {
    plugins: [tailwindcss()],
  },
});
