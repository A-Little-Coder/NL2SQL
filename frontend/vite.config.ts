/// <reference types="vitest" />
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import path from 'node:path';

// Vite 配置（决策 D8）
// - dev server :5173
// - proxy /api/v1 -> http://localhost:8000（FastAPI），规避跨域/凭据问题
// - Vitest 测试环境 jsdom
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  server: {
    port: 5173,
    strictPort: false,
    proxy: {
      '/api/v1': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
  test: {
    globals: true,
    environment: 'jsdom',
    setupFiles: './tests/setup.ts',
    css: false,
    // e2e/ 是 Playwright 测试（npm run e2e），不归 vitest 收集
    exclude: ['node_modules', 'dist', 'e2e'],
  },
});
